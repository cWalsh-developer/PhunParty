import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta

from app.schemas.auth_models import UserSession
from sqlalchemy import or_
from sqlalchemy.orm import Session

REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
PREVIOUS_TOKEN_GRACE_SECONDS = int(
    os.getenv("REFRESH_TOKEN_PREVIOUS_GRACE_SECONDS", "60")
)
SESSION_CLEANUP_RETENTION_DAYS = int(
    os.getenv("USER_SESSION_CLEANUP_RETENTION_DAYS", "7")
)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def create_refresh_session(
    db: Session,
    player_id: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    device_id: str | None = None,
    commit: bool = True,
) -> tuple[str, UserSession]:
    raw_token = generate_refresh_token()
    now = utc_now()
    record = UserSession(
        player_id=player_id,
        current_refresh_token_hash=hash_refresh_token(raw_token),
        user_agent=user_agent,
        ip_address=ip_address,
        device_id=device_id,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=REFRESH_TOKEN_DAYS),
    )

    db.add(record)
    db.flush()
    if commit:
        db.commit()
    return raw_token, record


def get_refresh_session_by_token(
    db: Session,
    raw_token: str,
    *,
    lock: bool = False,
) -> tuple[UserSession | None, str | None]:
    token_hash = hash_refresh_token(raw_token)
    query = db.query(UserSession).filter(
        or_(
            UserSession.current_refresh_token_hash == token_hash,
            UserSession.previous_refresh_token_hash == token_hash,
        )
    )
    if lock:
        query = query.with_for_update()
    record = query.first()

    if not record:
        return None, None

    now = utc_now()
    if record.revoked_at is not None:
        return None, "revoked"

    if record.expires_at <= now:
        return None, "expired"

    if record.current_refresh_token_hash == token_hash:
        return record, "current"

    if record.previous_refresh_token_hash == token_hash:
        if (
            record.previous_token_valid_until
            and record.previous_token_valid_until > now
        ):
            return record, "previous"
        return record, "reused_previous"

    return None, None


def rotate_refresh_session(
    db: Session,
    record: UserSession,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    device_id: str | None = None,
    commit: bool = True,
) -> tuple[str, UserSession]:
    raw_token = generate_refresh_token()
    now = utc_now()
    record.previous_refresh_token_hash = record.current_refresh_token_hash
    record.previous_token_valid_until = now + timedelta(
        seconds=PREVIOUS_TOKEN_GRACE_SECONDS
    )
    record.current_refresh_token_hash = hash_refresh_token(raw_token)
    record.user_agent = user_agent
    record.ip_address = ip_address
    if device_id is not None:
        record.device_id = device_id
    record.updated_at = now
    record.expires_at = now + timedelta(days=REFRESH_TOKEN_DAYS)

    db.flush()
    if commit:
        db.commit()
    return raw_token, record


def revoke_refresh_session(
    db: Session,
    record: UserSession,
    *,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    record.revoked_at = utc_now()
    record.revoke_reason = reason
    record.updated_at = utc_now()
    db.flush()
    if commit:
        db.commit()


def revoke_all_player_refresh_tokens(db: Session, player_id: str) -> None:
    now = utc_now()
    (
        db.query(UserSession)
        .filter(UserSession.player_id == player_id)
        .filter(UserSession.revoked_at.is_(None))
        .update(
            {
                "revoked_at": now,
                "revoke_reason": "password_changed",
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )
    db.commit()


def cleanup_stale_user_sessions(db: Session) -> int:
    cutoff = utc_now() - timedelta(days=SESSION_CLEANUP_RETENTION_DAYS)
    deleted = (
        db.query(UserSession)
        .filter(
            or_(
                UserSession.expires_at < cutoff,
                UserSession.revoked_at < cutoff,
            )
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


# Backwards-compatible names for existing auth route imports.
create_refresh_token = create_refresh_session
get_active_refresh_token = get_refresh_session_by_token
revoke_refresh_token = revoke_refresh_session
