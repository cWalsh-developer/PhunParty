import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

RLS_SETTINGS = (
    "app.current_player_id",
    "app.login_email",
    "app.reset_phone",
)


def _dialect_name(db: Session) -> str | None:
    try:
        bind = db.get_bind()
    except Exception:
        return None

    name = getattr(getattr(bind, "dialect", None), "name", None)
    return name if isinstance(name, str) else None


def _is_postgresql(db: Session) -> bool:
    return _dialect_name(db) == "postgresql"


def set_rls_current_player(db: Session, player_id: str) -> None:
    """
    Set the current authenticated player for PostgreSQL RLS policies.

    is_local=false keeps the setting across commits inside one request. Several
    existing CRUD helpers commit internally, so SET LOCAL would clear too early.
    """
    if not _is_postgresql(db):
        return

    db.execute(
        text("SELECT set_config('app.current_player_id', :player_id, false)"),
        {"player_id": player_id},
    )


def set_rls_login_email(db: Session, email: str) -> None:
    if not _is_postgresql(db):
        return

    db.execute(
        text("SELECT set_config('app.login_email', :email, false)"),
        {"email": email.strip().lower()},
    )


def set_rls_reset_phone(db: Session, phone: str) -> None:
    if not _is_postgresql(db):
        return

    db.execute(
        text("SELECT set_config('app.reset_phone', :phone, false)"),
        {"phone": phone.strip()},
    )


def clear_rls_context(db: Session) -> None:
    if not _is_postgresql(db):
        return

    try:
        db.rollback()

        for setting in RLS_SETTINGS:
            db.execute(text(f"RESET {setting}"))

        db.commit()
    except Exception:
        logger.exception("Failed to clear RLS context")
        db.rollback()


# Backwards-compatible name for earlier imports in this branch.
set_rls_player_context = set_rls_current_player
