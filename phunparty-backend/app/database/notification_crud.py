from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.schemas.players_model import Players
from app.schemas.social_models import Notification, UserPushToken


def create_notification(
    db: Session,
    recipient_player_id: str,
    notification_type: str,
    title: str,
    body: str,
    actor_player_id: Optional[str] = None,
    data: Optional[dict] = None,
) -> Notification:
    notification = Notification(
        recipient_player_id=recipient_player_id,
        actor_player_id=actor_player_id,
        type=notification_type,
        title=title,
        body=body,
        data=data or {},
    )
    db.add(notification)
    db.flush()
    return notification


def list_notifications(
    db: Session, player_id: str, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    query = db.query(Notification).filter(Notification.recipient_player_id == player_id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    return query.order_by(Notification.created_at.desc()).limit(limit).all()


def mark_notification_read(
    db: Session, player_id: str, notification_id: str
) -> Optional[Notification]:
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id)
        .filter(Notification.recipient_player_id == player_id)
        .first()
    )
    if not notification:
        return None

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.utcnow()
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_notifications_read(db: Session, player_id: str) -> int:
    notifications = (
        db.query(Notification)
        .filter(Notification.recipient_player_id == player_id)
        .filter(Notification.is_read == False)
        .all()
    )
    now = datetime.utcnow()
    for notification in notifications:
        notification.is_read = True
        notification.read_at = now
    db.commit()
    return len(notifications)


def register_push_token(
    db: Session,
    player_id: str,
    expo_push_token: str,
    device_id: Optional[str] = None,
    platform: Optional[str] = None,
) -> UserPushToken:
    token = (
        db.query(UserPushToken)
        .filter(UserPushToken.expo_push_token == expo_push_token)
        .first()
    )
    now = datetime.utcnow()
    if token:
        token.player_id = player_id
        token.device_id = device_id
        token.platform = platform
        token.is_active = True
        token.updated_at = now
    else:
        token = UserPushToken(
            player_id=player_id,
            expo_push_token=expo_push_token,
            device_id=device_id,
            platform=platform,
            updated_at=now,
        )
        db.add(token)

    db.commit()
    db.refresh(token)
    return token


def get_active_push_tokens(db: Session, player_id: str) -> list[str]:
    return [
        token.expo_push_token
        for token in db.query(UserPushToken)
        .filter(UserPushToken.player_id == player_id)
        .filter(UserPushToken.is_active == True)
        .all()
    ]


def update_notification_settings(
    db: Session,
    player: Players,
    friend_request_notifications_enabled: Optional[bool] = None,
    allow_friend_code_search: Optional[bool] = None,
    allow_phone_discovery: Optional[bool] = None,
) -> Players:
    if friend_request_notifications_enabled is not None:
        player.friend_request_notifications_enabled = (
            friend_request_notifications_enabled
        )
    if allow_friend_code_search is not None:
        player.allow_friend_code_search = allow_friend_code_search
    if allow_phone_discovery is not None:
        player.allow_phone_discovery = allow_phone_discovery

    db.commit()
    db.refresh(player)
    return player
