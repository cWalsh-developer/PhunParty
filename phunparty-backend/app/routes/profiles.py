from app.database.friend_crud import are_friends, ensure_player_friend_code
from app.database.presence_crud import (
    get_presence_map,
    visible_presence_for_player,
)
from app.dependencies import get_current_player, get_db
from app.models.friends import FriendProfileResponse
from app.schemas.players_model import Players
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

    ensure_player_friend_code(db, player)
    relationship_status = (
        "self"
        if current_player.player_id == player.player_id
        else "friends"
        if are_friends(db, current_player.player_id, player.player_id)
        else "none"
    )
    presence = get_presence_map(db, [player.player_id]).get(player.player_id)
    is_online, last_seen_at = visible_presence_for_player(player, presence)

    return FriendProfileResponse(
        player_id=player.player_id,
        player_name=player.player_name,
        player_email=player.player_email,
        player_mobile=player.player_mobile,
        profile_photo_url=player.profile_photo_url,
        friend_code=player.friend_code,
        relationship_status=relationship_status,
        profile_visibility=player.profile_visibility,
        can_view_profile=True,
        show_online_status=player.show_online_status,
        is_online=is_online,
        last_seen_at=last_seen_at,
    )
