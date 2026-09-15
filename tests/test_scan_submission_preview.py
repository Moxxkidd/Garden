import socket

import pytest

from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.scan import ScanOptions
from tests.test_assessment_application import build_holding_service


def test_preview_resolves_actual_options_without_dispatch_or_network(tmp_path, monkeypatch):
    from app.services.scan_submission_preview import build_preview

    def unexpected_network(*args, **kwargs):
        raise AssertionError("preview must not contact the target")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_network)
    settings = get_settings().model_copy(update={"scan_overall_timeout_seconds": 123.0})
    request = AssessmentStartRequest(
        url="https://EXAMPLE.com/path?token=private-value#private-fragment",
        options=ScanOptions(max_pages=7, max_depth=1, retry_attempts=0),
    )
    service, dispatcher = build_holding_service(tmp_path)
    with session_scope() as session:
        preview = build_preview(session, request, settings)
        again = build_preview(session, request, settings)
    assert preview.can_submit
    assert preview.effective_options.max_pages == 7
    assert preview.effective_options.max_depth == 1
    assert preview.effective_options.retry_attempts == 0
    assert preview.effective_options.overall_timeout_seconds == 123.0
    assert preview.entry_display == "https://example.com/path?token=[REDACTED]"
    assert [context.kind for context in preview.contexts] == ["anonymous"]
    assert "private-value" not in preview.model_dump_json()
    assert "private-fragment" not in preview.model_dump_json()
    assert preview.configuration_fingerprint == again.configuration_fingerprint
    assert service.list_scans() == []
    assert dispatcher.ids == []


def test_authenticated_preview_checks_roles_and_does_not_resolve_secrets(monkeypatch):
    from app.services.scan_submission_preview import build_preview
    from app.services.secret_resolver import SecretResolver
    from tests.test_coverage_wizard import _setup_records

    setup = _setup_records()
    request = AssessmentStartRequest(
        url=setup.url,
        mode="authenticated_coverage",
        target_id=setup.target_id,
        user_profile_id=setup.user_id,
        admin_profile_id=setup.admin_id,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not resolve credentials")

    monkeypatch.setattr(SecretResolver, "resolve", forbidden)
    with session_scope() as session:
        preview = build_preview(session, request, get_settings())
    assert preview.can_submit
    assert [context.kind for context in preview.contexts] == ["anonymous", "user", "admin"]
    assert preview.contexts[1].profile_id == setup.user_id
    assert "请求记录" in preview.budget_note
    assert "登录" in " ".join(issue.message for issue in preview.issues)


@pytest.mark.parametrize(
    "fault", ["roles", "same_profile", "missing", "target", "origin", "config", "config_origin"]
)
def test_authenticated_preview_blocks_known_configuration_errors(fault):
    from app.models.credential_profile import CredentialProfile
    from app.services.login_configs import encode_inline_login_config
    from app.services.scan_submission_preview import build_preview
    from tests.test_coverage_wizard import _setup_records

    setup = _setup_records()
    data = dict(
        url=setup.url,
        mode="authenticated_coverage",
        target_id=setup.target_id,
        user_profile_id=setup.user_id,
        admin_profile_id=setup.admin_id,
    )
    if fault == "roles":
        data.update(user_profile_id=setup.admin_id, admin_profile_id=setup.user_id)
    elif fault == "same_profile":
        data["admin_profile_id"] = setup.user_id
    elif fault == "missing":
        data["user_profile_id"] = 99999
    elif fault == "target":
        data["target_id"] = 99999
    elif fault == "origin":
        data["url"] = "https://different.example/?token=private-error-secret"
    with session_scope() as session:
        user = session.get(CredentialProfile, setup.user_id)
        if fault == "config":
            user.login_config_path = "inline://private-error-secret"
        if fault == "config_origin":
            user.login_config_path = encode_inline_login_config(
                dict(
                    adapter="playwright",
                    login_url="https://different.example/?token=private-error-secret",
                    validate_url="/",
                )
            )
        preview = build_preview(session, AssessmentStartRequest(**data), get_settings())
    assert not preview.can_submit
    assert any(issue.severity == "error" for issue in preview.issues)
    assert "private-error-secret" not in preview.model_dump_json()


def test_preview_fingerprint_detects_config_change_but_not_secret_replacement():
    from app.models.credential_profile import CredentialProfile
    from app.services.login_configs import encode_inline_login_config
    from app.services.scan_submission_preview import build_preview
    from tests.test_coverage_wizard import _setup_records

    setup = _setup_records()
    request = AssessmentStartRequest(
        url=setup.url,
        mode="authenticated_coverage",
        user_profile_id=setup.user_id,
        admin_profile_id=setup.admin_id,
    )
    with session_scope() as session:
        original = build_preview(session, request, get_settings())
        user = session.get(CredentialProfile, setup.user_id)
        user.secret_ref = "env://replacement-secret"
        refreshed = build_preview(session, request, get_settings())
        assert original.configuration_fingerprint == refreshed.configuration_fingerprint
        user.login_config_path = encode_inline_login_config(
            dict(adapter="playwright", login_url="/different-login", validate_url="/")
        )
        changed = build_preview(session, request, get_settings())
        assert changed.can_submit
        assert original.configuration_fingerprint != changed.configuration_fingerprint


@pytest.mark.parametrize(
    "url",
    [
        "https://user:private-error-secret@example.com/",
        "http://example.com:99999",
        "file:///etc/passwd",
    ],
)
def test_invalid_entry_is_a_safe_field_error(url):
    from app.services.scan_submission_preview import build_preview

    with session_scope() as session:
        preview = build_preview(session, AssessmentStartRequest(url=url), get_settings())
    assert not preview.can_submit
    assert any(issue.field == "url" and issue.severity == "error" for issue in preview.issues)
    assert "private-error-secret" not in preview.model_dump_json()


def test_explicit_resolved_options_have_same_preview_fingerprint():
    from app.services.scan_submission_preview import build_preview

    request = AssessmentStartRequest(url="http://127.0.0.1/")
    with session_scope() as session:
        initial = build_preview(session, request, get_settings())
        resolved = build_preview(
            session,
            request.model_copy(update={"options": initial.effective_options}),
            get_settings(),
        )
    assert initial.configuration_fingerprint == resolved.configuration_fingerprint


@pytest.mark.parametrize(
    "field,value", [("username", "changed-private-identity"), ("auth_type", "bearer_token")]
)
def test_login_identity_change_invalidates_preview_without_displaying_identity(field, value):
    from app.models.credential_profile import CredentialProfile
    from app.services.scan_submission_preview import build_preview
    from tests.test_coverage_wizard import _setup_records

    setup = _setup_records()
    request = AssessmentStartRequest(
        url=setup.url,
        mode="authenticated_coverage",
        user_profile_id=setup.user_id,
        admin_profile_id=setup.admin_id,
    )
    with session_scope() as session:
        before = build_preview(session, request, get_settings())
        setattr(session.get(CredentialProfile, setup.user_id), field, value)
        after = build_preview(session, request, get_settings())
    assert before.configuration_fingerprint != after.configuration_fingerprint
    assert value not in after.model_dump_json()
