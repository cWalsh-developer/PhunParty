from app.database.presence_crud import set_player_offline, set_player_online
from app.dependencies import get_current_player, get_db
from app.models.presence import PresenceResponse
from app.schemas.players_model import Players
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter()


@router.post("/heartbeat", response_model=PresenceResponse)
def heartbeat(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    presence = set_player_online(db, current_player.player_id)
    return PresenceResponse(
        player_id=current_player.player_id,
        is_online=current_player.show_online_status,
        show_online_status=current_player.show_online_status,
        last_seen_at=presence.last_seen_at,
    )


@router.post("/offline", response_model=PresenceResponse)
def offline(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    presence = set_player_offline(db, current_player.player_id)
    return PresenceResponse(
        player_id=current_player.player_id,
        is_online=False,
        show_online_status=current_player.show_online_status,
        last_seen_at=presence.last_seen_at,
    )
