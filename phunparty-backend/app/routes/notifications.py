from app.database.notification_crud import (
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    register_push_token,
    update_notification_settings,
)
from app.dependencies import get_current_player, get_db
from app.models.notifications import (
    NotificationListResponse,
    NotificationResponse,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
    PushTokenRegistrationRequest,
    PushTokenResponse,
)
from app.schemas.players_model import Players
from app.security.cache import invalidate_profile_cache
from app.security.rate_limit import enforce_rate_limit
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

router = APIRouter()


@router.post("/register-push-token", response_model=PushTokenResponse)
async def register_push_token_route(
    http_request: Request,
    request: PushTokenRegistrationRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        http_request,
        scope="notifications-push-token-player",
        identifier=current_player.player_id,
        limit=20,
        window_seconds=3600,
    )
    try:
        return register_push_token(
            db,
            current_player.player_id,
            request.expo_push_token,
            request.device_id,
            request.platform,
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Unable to register push token",
        )


@router.get("", response_model=NotificationListResponse)
@router.get("/", response_model=NotificationListResponse)
async def get_notifications(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="notifications-list-player",
        identifier=current_player.player_id,
        limit=120,
        window_seconds=300,
    )
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
async def read_all_notifications(
    request: Request,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="notifications-read-all-player",
        identifier=current_player.player_id,
        limit=30,
        window_seconds=3600,
    )
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
    invalidate_profile_cache(current_player.player_id)
    return NotificationSettingsResponse(
        friend_request_notifications_enabled=(
            player.friend_request_notifications_enabled
        ),
        allow_friend_code_search=player.allow_friend_code_search,
        allow_phone_discovery=player.allow_phone_discovery,
    )
