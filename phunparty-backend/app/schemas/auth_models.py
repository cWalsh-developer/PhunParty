import uuid
from datetime import UTC, datetime

from app.config import Base
from sqlalchemy import Column, DateTime, ForeignKey, Index, String


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def uuid_text() -> str:
    return str(uuid.uuid4())


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True, default=uuid_text)
    player_id = Column(
        String,
        ForeignKey("players.player_id"),
        nullable=False,
        index=True,
    )
    current_refresh_token_hash = Column(String, nullable=False, unique=True)
    previous_refresh_token_hash = Column(String, nullable=True, unique=True)
    previous_token_valid_until = Column(DateTime, nullable=True)
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    device_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
    revoke_reason = Column(String, nullable=True)


Index(
    "idx_user_sessions_active_player",
    UserSession.player_id,
    postgresql_where=UserSession.revoked_at.is_(None),
)
