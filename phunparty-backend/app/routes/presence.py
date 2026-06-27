from app.database.presence_crud import set_player_offline, set_player_online
from app.dependencies import get_current_player, get_db
from app.models.presence import PresenceResponse
from app.schemas.players_model import Players
from app.security.cache import invalidate_friends_presence_cache
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

router = APIRouter()


@router.post("/heartbeat", response_model=PresenceResponse)
async def heartbeat(
    request: Request,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="presence-heartbeat-ip",
        identifier=get_client_ip(request),
        limit=120,
        window_seconds=300,
    )
    await enforce_rate_limit(
        request,
        scope="presence-heartbeat-player",
        identifier=current_player.player_id,
        limit=60,
        window_seconds=300,
    )
    presence = set_player_online(db, current_player.player_id)
    return PresenceResponse(
        player_id=current_player.player_id,
        is_online=current_player.show_online_status,
        show_online_status=current_player.show_online_status,
        last_seen_at=presence.last_seen_at,
    )


@router.post("/offline", response_model=PresenceResponse)
async def offline(
    request: Request,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="presence-offline-ip",
        identifier=get_client_ip(request),
        limit=120,
        window_seconds=300,
    )
    await enforce_rate_limit(
        request,
        scope="presence-offline-player",
        identifier=current_player.player_id,
        limit=30,
        window_seconds=300,
    )
    presence = set_player_offline(db, current_player.player_id)
    invalidate_friends_presence_cache(db, current_player.player_id)
    return PresenceResponse(
        player_id=current_player.player_id,
        is_online=False,
        show_online_status=current_player.show_online_status,
        last_seen_at=presence.last_seen_at,
    )
