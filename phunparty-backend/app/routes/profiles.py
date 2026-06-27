from app.database.friend_crud import are_friends, ensure_player_friend_code
from app.database.presence_crud import (
    get_presence_map,
    visible_presence_for_player,
)
from app.database.profile_stats_crud import get_player_stats_summary
from app.dependencies import get_current_player, get_db
from app.models.friends import FriendProfileResponse
from app.models.profile_stats import ProfileStatsResponse
from app.schemas.players_model import Players
from app.security.cache import (
    cache,
    profile_cache_key,
    profile_stats_cache_key,
)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

router = APIRouter()


def can_view_profile(db: Session, viewer_id: str, player: Players) -> bool:
    if viewer_id == player.player_id:
        return True

    if player.profile_visibility == "public":
        return True

    if player.profile_visibility == "friends":
        return are_friends(db, viewer_id, player.player_id)

    return False


@router.get("/{player_id}", response_model=FriendProfileResponse)
def get_profile(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    cache_key = profile_cache_key(current_player.player_id, player_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    player = (
        db.query(Players)
        .filter(Players.player_id == player_id)
        .filter(Players.is_deleted == False)
        .filter(Players.is_deactivated == False)
        .first()
    )

    if not player:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not can_view_profile(db, current_player.player_id, player):
        raise HTTPException(
            status_code=403,
            detail="This player has not made their profile visible to you",
        )

    is_self_profile = current_player.player_id == player.player_id
    if is_self_profile:
        ensure_player_friend_code(db, player)

    relationship_status = (
        "self"
        if is_self_profile
        else (
            "friends"
            if are_friends(db, current_player.player_id, player.player_id)
            else "none"
        )
    )
    presence = get_presence_map(db, [player.player_id]).get(player.player_id)
    is_online, last_seen_at = visible_presence_for_player(player, presence)

    response = FriendProfileResponse(
        player_id=player.player_id,
        player_name=player.player_name,
        player_email=player.player_email if is_self_profile else None,
        player_mobile=player.player_mobile if is_self_profile else None,
        profile_photo_url=player.profile_photo_url,
        friend_code=player.friend_code if is_self_profile else None,
        relationship_status=relationship_status,
        profile_visibility=player.profile_visibility,
        can_view_profile=True,
        can_view_game_stats=player.share_game_stats,
        share_game_stats=player.share_game_stats,
        show_online_status=player.show_online_status,
        is_online=is_online,
        last_seen_at=last_seen_at,
    )
    cache.set(cache_key, response.model_dump(mode="json"), ttl_seconds=45)
    return response


@router.get("/{player_id}/stats", response_model=ProfileStatsResponse)
def get_profile_stats(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    cache_key = profile_stats_cache_key(current_player.player_id, player_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    player = (
        db.query(Players)
        .filter(Players.player_id == player_id)
        .filter(Players.is_deleted == False)
        .filter(Players.is_deactivated == False)
        .first()
    )

    if not player:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not can_view_profile(db, current_player.player_id, player):
        raise HTTPException(
            status_code=403,
            detail="This player has not made their profile visible to you",
        )

    if not player.share_game_stats:
        raise HTTPException(
            status_code=403,
            detail="This player has not made their game stats visible",
        )

    response = ProfileStatsResponse(**get_player_stats_summary(db, player.player_id))
    cache.set(cache_key, response.model_dump(mode="json"), ttl_seconds=180)
    return response
