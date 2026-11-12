"""Read-only guidance over login attempts and saved restoration facts."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.auth_session import AuthSession
from app.models.login_attempt import LoginAttempt

LABELS = {
    "login": "待登录",
    "verify": "待验证",
    "ready": "可用",
    "invalid": "失效",
    "unknown": "无法确认",
}


def identity_flow(session, profile_id):
    rows = list(
        session.scalars(
            select(AuthSession)
            .where(
                AuthSession.credential_profile_id == profile_id,
                AuthSession.identity_metadata.is_not(None),
            )
            .order_by(AuthSession.id.desc())
        )
    )
    now = datetime.now(timezone.utc)

    def usable(r):
        return (
            r.status == "active"
            and not r.revoked_at
            and r.last_validated_at
            and (not r.expires_at or r.expires_at.replace(tzinfo=timezone.utc) > now)
        )

    current = next((r for r in rows if usable(r)), rows[0] if rows else None)
    attempt = session.scalar(
        select(LoginAttempt)
        .where(LoginAttempt.profile_id == profile_id)
        .order_by(LoginAttempt.id.desc())
        .limit(1)
    )
    if current:
        state = (
            "ready"
            if usable(current)
            else "invalid"
            if current.revoked_at
            or current.status == "invalid"
            or (current.expires_at and current.expires_at.replace(tzinfo=timezone.utc) <= now)
            else "unknown"
        )
    else:
        legacy = session.scalar(
            select(AuthSession.id).where(AuthSession.credential_profile_id == profile_id).limit(1)
        )
        state = "unknown" if legacy else "login"
    if (
        state != "ready"
        and attempt
        and attempt.state in {"created", "waiting_for_user", "validating_restore"}
    ):
        state = "verify" if attempt.state == "validating_restore" else "login"
    return dict(
        state=state,
        label=LABELS[state],
        session_id=current.id if current else None,
        checked_at=current.last_validated_at if current else None,
        replacement=dict(id=attempt.id, state=attempt.state, reason_code=attempt.reason_code)
        if attempt
        else None,
    )
