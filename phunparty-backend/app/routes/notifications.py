from app.database.notification_crud import (list_notifications,
                                            mark_all_notifications_read,
                                            mark_notification_read,
                                            register_push_token,
                                            update_notification_settings)
from app.dependencies import get_current_player, get_db
from app.models.notifications import (NotificationListResponse,
                                      NotificationResponse,
                                      NotificationSettingsResponse,
                                      NotificationSettingsUpdate,
                                      PushTokenRegistrationRequest,
                                      PushTokenResponse)
from app.schemas.players_model import Players
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

router = APIRouter()


@router.post("/register-push-token", response_model=PushTokenResponse)
def register_push_token_route(
    request: PushTokenRegistrationRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    return register_push_token(
        db,
        current_player.player_id,
        request.expo_push_token,
        request.device_id,
        request.platform,
    )


@router.get("", response_model=NotificationListResponse)
@router.get("/", response_model=NotificationListResponse)
def get_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    notifications = list_notifications(
        db,
        current_player.player_id,
        unread_only=unread_only,
        limit=limit,
    )
    return NotificationListResponse(
        notifications=[
            NotificationResponse.model_validate(item) for item in notifications
        ]
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def read_notification(
    notification_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    notification = mark_notification_read(db, current_player.player_id, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification


@router.post("/read-all")
def read_all_notifications(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    count = mark_all_notifications_read(db, current_player.player_id)
    return {"detail": "Notifications marked read", "count": count}


@router.patch("/settings", response_model=NotificationSettingsResponse)
def update_settings(
    request: NotificationSettingsUpdate,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    player = update_notification_settings(
        db,
        current_player,
        friend_request_notifications_enabled=(
            request.friend_request_notifications_enabled
        ),
        allow_friend_code_search=request.allow_friend_code_search,
        allow_phone_discovery=request.allow_phone_discovery,
    )
    return NotificationSettingsResponse(
        friend_request_notifications_enabled=(
            player.friend_request_notifications_enabled
        ),
        allow_friend_code_search=player.allow_friend_code_search,
        allow_phone_discovery=player.allow_phone_discovery,
    )
