from app.dependencies import get_current_player, get_db
from app.models.privacy import PrivacySettingsResponse, PrivacySettingsUpdate
from app.schemas.players_model import Players
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter()


def privacy_settings_response(player: Players) -> PrivacySettingsResponse:
    return PrivacySettingsResponse(
        profile_visibility=player.profile_visibility,
        show_online_status=player.show_online_status,
        allow_friend_requests=player.allow_friend_requests,
        share_game_stats=player.share_game_stats,
        data_collection=player.data_collection,
        crash_reporting=player.crash_reporting,
    )


@router.get("/settings", response_model=PrivacySettingsResponse)
def get_privacy_settings(
    current_player: Players = Depends(get_current_player),
):
    return privacy_settings_response(current_player)


@router.patch("/settings", response_model=PrivacySettingsResponse)
def update_privacy_settings(
    request: PrivacySettingsUpdate,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    updates = request.model_dump(exclude_unset=True)

    for key, value in updates.items():
        setattr(current_player, key, value)

    db.commit()
    db.refresh(current_player)
    return privacy_settings_response(current_player)
