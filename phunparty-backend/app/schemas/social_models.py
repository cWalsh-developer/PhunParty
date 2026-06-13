import uuid
from datetime import UTC, datetime

from app.config import Base
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)


def uuid_text() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class FriendRequest(Base):
    __tablename__ = "friend_requests"

    id = Column(String, primary_key=True, default=uuid_text)
    sender_player_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    receiver_player_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    status = Column(String, nullable=False, default="pending", index=True)
    message = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    responded_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "sender_player_id <> receiver_player_id",
            name="ck_friend_requests_not_self",
        ),
    )


class Friendship(Base):
    __tablename__ = "friendships"

    id = Column(String, primary_key=True, default=uuid_text)
    player_low_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    player_high_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "player_low_id <> player_high_id",
            name="ck_friendships_not_self",
        ),
        UniqueConstraint(
            "player_low_id",
            "player_high_id",
            name="uq_friendship_pair",
        ),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True, default=uuid_text)
    recipient_player_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    actor_player_id = Column(
        String, ForeignKey("players.player_id"), nullable=True, index=True
    )
    type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    body = Column(String, nullable=False)
    data = Column(JSON, nullable=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    read_at = Column(DateTime, nullable=True)


class UserPushToken(Base):
    __tablename__ = "user_push_tokens"

    id = Column(String, primary_key=True, default=uuid_text)
    player_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    expo_push_token = Column(String, nullable=False, unique=True, index=True)
    device_id = Column(String, nullable=True)
    platform = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, nullable=False)


class PlayerPresence(Base):
    __tablename__ = "player_presence"

    player_id = Column(
        String, ForeignKey("players.player_id"), primary_key=True, nullable=False
    )
    is_online = Column(Boolean, default=False, nullable=False, index=True)
    last_seen_at = Column(DateTime, default=utc_now, nullable=False, index=True)
    updated_at = Column(DateTime, default=utc_now, nullable=False)
