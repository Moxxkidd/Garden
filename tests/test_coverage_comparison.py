import pytest
from sqlalchemy import func, select

from app.db.bootstrap import get_session
from app.models.coverage_difference import CoverageDifference
from app.services.coverage_comparison import CoverageComparisonService
from tests.helpers.assessment import add_asset, make_authenticated_run


@pytest.fixture
def db_session():
    with get_session() as session:
        yield session
        session.rollback()


def _complete_contexts(run) -> dict[str, object]:
    contexts = {context.kind: context for context in run.contexts}
    for context in contexts.values():
        context.status = "completed"
        context.collection_status = "completed"
        context.completeness = "complete"
    return contexts


def test_complete_contexts_classify_admin_only(db_session) -> None:
    run = make_authenticated_run(db_session)
    contexts = _complete_contexts(run)
    add_asset(
        db_session,
        run,
        context_id=contexts["admin"].id,
        identity_key="GET:/admin",
        url="http://127.0.0.1:8000/admin",
        status_code=200,
        title="Admin",
        attributes={
            "content_type": "text/html",
            "content_signature": "stable-admin-signature",
        },
    )

    summary = CoverageComparisonService().compare(db_session, run)

    assert summary.counts == {"admin_only": 1}
    item = summary.differences[0]
    assert item.identity_key == "GET:/admin"
    assert item.classification == "admin_only"
    assert (item.anonymous_state, item.user_state, item.admin_state) == (
        "absent",
        "absent",
        "present",
    )
    assert (item.anonymous_present, item.user_present, item.admin_present) == (
        False,
        False,
        True,
    )


def test_failed_user_context_is_unknown_not_absent(db_session) -> None:
    run = make_authenticated_run(db_session)
    contexts = _complete_contexts(run)
    contexts["user"].status = "failed"
    contexts["user"].collection_status = "failed"
    contexts["user"].completeness = "incomplete"
    add_asset(
        db_session,
        run,
        context_id=contexts["admin"].id,
        identity_key="GET:/admin",
        url="http://127.0.0.1:8000/admin",
    )

    item = CoverageComparisonService().compare(db_session, run).differences[0]

    assert item.user_state == "unknown"
    assert item.user_present is None
    assert item.classification == "unknown"
    assert item.confidence == "low"


@pytest.mark.parametrize(
    ("present_kinds", "expected"),
    [
        (("anonymous", "user", "admin"), "shared"),
        (("user", "admin"), "user_only"),
        (("user",), "inconsistent"),
        (("anonymous",), "unexpectedly_anonymous"),
        (("anonymous", "user"), "unexpectedly_anonymous"),
        (("anonymous", "admin"), "unexpectedly_anonymous"),
    ],
)
def test_complete_context_presence_classification_table(
    db_session,
    present_kinds: tuple[str, ...],
    expected: str,
) -> None:
    run = make_authenticated_run(db_session)
    contexts = _complete_contexts(run)
    for kind in present_kinds:
        add_asset(
            db_session,
            run,
            context_id=contexts[kind].id,
            identity_key="GET:/matrix",
            url="http://127.0.0.1:8000/matrix",
            status_code=200,
            title="Matrix",
            attributes={
                "content_type": "text/html",
                "content_signature": "same-signature",
            },
        )

    item = CoverageComparisonService().compare(db_session, run).differences[0]

    assert item.classification == expected
    assert item.confidence == "high"


def test_incomplete_context_keeps_observed_presence_but_unknown_for_missing_assets(
    db_session,
) -> None:
    run = make_authenticated_run(db_session)
    contexts = _complete_contexts(run)
    contexts["user"].status = "failed"
    contexts["user"].collection_status = "failed"
    contexts["user"].completeness = "incomplete"
    for kind in ("user", "admin"):
        add_asset(
            db_session,
            run,
            context_id=contexts[kind].id,
            identity_key="GET:/partially-seen",
        )

    item = CoverageComparisonService().compare(db_session, run).differences[0]

    assert (item.anonymous_state, item.user_state, item.admin_state) == (
        "absent",
        "present",
        "present",
    )
    assert item.classification == "user_only"


def test_all_present_with_different_stable_response_is_medium_inconsistent(db_session) -> None:
    run = make_authenticated_run(db_session)
    contexts = _complete_contexts(run)
    for kind, signature in (
        ("anonymous", "public"),
        ("user", "user"),
        ("admin", "admin"),
    ):
        add_asset(
            db_session,
            run,
            context_id=contexts[kind].id,
            identity_key="GET:/profile",
            url="http://127.0.0.1:8000/profile",
            status_code=200,
            title="Profile",
            attributes={
                "content_type": "application/json",
                "content_signature": signature,
            },
        )

    item = CoverageComparisonService().compare(db_session, run).differences[0]

    assert item.classification == "inconsistent"
    assert item.confidence == "medium"


def test_compare_is_idempotent_and_context_summaries_are_allowlisted(db_session) -> None:
    run = make_authenticated_run(db_session)
    contexts = _complete_contexts(run)
    forbidden_values = {
        "authorization": "Bearer raw-secret",
        "cookie": "session=raw-secret",
        "response_text": "raw private response",
        "protected_storage_ref": "vault://private/payload",
    }
    add_asset(
        db_session,
        run,
        context_id=contexts["admin"].id,
        identity_key="GET:/items?id&token",
        url="http://127.0.0.1:8000/items?id=7&token=raw-query-secret#private",
        status_code=200,
        title="Items",
        attributes={
            "content_type": "application/json",
            "content_signature": "safe-signature",
            **forbidden_values,
        },
    )

    first = CoverageComparisonService().compare(db_session, run)
    second = CoverageComparisonService().compare(db_session, run)

    row_count = db_session.scalar(
        select(func.count(CoverageDifference.id)).where(CoverageDifference.scan_run_id == run.id)
    )
    assert row_count == 1
    assert len(first.differences) == len(second.differences) == 1
    summary = second.differences[0].context_summaries["admin"]
    assert set(summary) == {
        "asset_id",
        "status_code",
        "url",
        "title",
        "content_type",
        "content_signature",
    }
    assert summary["url"] == ("http://127.0.0.1:8000/items?id=[REDACTED]&token=[REDACTED]")
    rendered = str(summary)
    assert "raw-query-secret" not in rendered
    assert all(value not in rendered for value in forbidden_values.values())
