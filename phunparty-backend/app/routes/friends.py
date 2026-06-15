from app.security.rls import set_rls_current_player
from app.database.friend_crud import (
    accept_friend_request,
    ensure_player_friend_code,
    get_player_by_friend_code,
    get_player_public_profile,
    get_relationship_status,
    list_friends,
    list_incoming_requests,
    list_outgoing_requests,
    reject_friend_request,
    remove_friendship,
    send_friend_request,
)
from app.database.notification_crud import get_active_push_tokens
from app.database.presence_crud import get_presence_map, visible_presence_for_player
from app.dependencies import get_current_player, get_db
from app.models.friends import (
    FriendCodeResponse,
    FriendProfileResponse,
    FriendRequestCreate,
    FriendRequestResponse,
    FriendSearchRequest,
    FriendsListResponse,
)
from app.models.presence import FriendsPresenceResponse, PresenceResponse
from app.schemas.players_model import Players
from app.schemas.social_models import FriendRequest
from app.security.cache import (
    cache,
    friends_cache_key,
    friends_presence_cache_key,
    invalidate_social_cache,
)
from app.security.rate_limit import enforce_rate_limit
from app.utils.expo_push import send_expo_push_to_tokens
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def profile_response(
    db: Session,
    current_player_id: str,
    player: Players,
    relationship_status: str | None = None,
) -> FriendProfileResponse:
    is_self_profile = current_player_id == player.player_id
    if is_self_profile:
        ensure_player_friend_code(db, player)
    return FriendProfileResponse(
        player_id=player.player_id,
        player_name=player.player_name,
        profile_photo_url=player.profile_photo_url,
        friend_code=player.friend_code if is_self_profile else None,
        relationship_status=relationship_status
        or get_relationship_status(db, current_player_id, player.player_id),
    )


def request_response(
    db: Session, current_player_id: str, friend_request: FriendRequest
) -> FriendRequestResponse:
    sender = get_player_public_profile(db, friend_request.sender_player_id)
    receiver = get_player_public_profile(db, friend_request.receiver_player_id)
    return FriendRequestResponse(
        id=friend_request.id,
        sender_player_id=friend_request.sender_player_id,
        receiver_player_id=friend_request.receiver_player_id,
        status=friend_request.status,
        message=friend_request.message,
        created_at=friend_request.created_at,
        responded_at=friend_request.responded_at,
        sender=profile_response(db, current_player_id, sender) if sender else None,
        receiver=(
            profile_response(db, current_player_id, receiver) if receiver else None
        ),
    )


@router.get("/me/code", response_model=FriendCodeResponse)
def get_my_friend_code(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    current_player = ensure_player_friend_code(db, current_player)
    return FriendCodeResponse(
        friend_code=current_player.friend_code,
        allow_friend_code_search=current_player.allow_friend_code_search,
        allow_phone_discovery=current_player.allow_phone_discovery,
        friend_request_notifications_enabled=(
            current_player.friend_request_notifications_enabled
        ),
    )


@router.post("/search", response_model=FriendProfileResponse)
async def search_by_friend_code(
    http_request: Request,
    request: FriendSearchRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        http_request,
        scope="friends-search-player",
        identifier=current_player.player_id,
        limit=30,
        window_seconds=3600,
    )
    current_player = ensure_player_friend_code(db, current_player)
    player = get_player_by_friend_code(db, request.friend_code)
    if not player:
        raise HTTPException(status_code=404, detail="No player found with that code")
    return profile_response(db, current_player.player_id, player)


@router.post("/requests", response_model=FriendRequestResponse)
async def create_friend_request(
    http_request: Request,
    request: FriendRequestCreate,
    background_tasks: BackgroundTasks,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        http_request,
        scope="friends-request-player",
        identifier=current_player.player_id,
        limit=20,
        window_seconds=3600,
    )
    logger.warning(
        "Creating friend request sender=%s receiver_code=%s",
        current_player.player_id,
        request.friend_code,
    )

    try:
        friend_request = send_friend_request(
            db, current_player, request.friend_code, request.message
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    set_rls_current_player(db, current_player.player_id)

    receiver = get_player_public_profile(db, friend_request.receiver_player_id)

    if receiver:
        invalidate_social_cache(current_player.player_id, receiver.player_id)
        tokens = get_active_push_tokens(db, receiver.player_id)

        logger.warning(
            "Friend request push lookup sender=%s receiver=%s notifications_enabled=%s token_count=%s",
            current_player.player_id,
            receiver.player_id,
            receiver.friend_request_notifications_enabled,
            len(tokens),
        )

        if receiver.friend_request_notifications_enabled and tokens:
            background_tasks.add_task(
                send_expo_push_to_tokens,
                tokens,
                "New friend request",
                f"{current_player.player_name} wants to add you as a friend",
                {
                    "type": "friend_request_received",
                    "friend_request_id": friend_request.id,
                    "sender_player_id": current_player.player_id,
                },
            )

    return request_response(db, current_player.player_id, friend_request)


@router.get("/requests/incoming", response_model=list[FriendRequestResponse])
def get_incoming_requests(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    return [
        request_response(db, current_player.player_id, friend_request)
        for friend_request in list_incoming_requests(db, current_player.player_id)
    ]


@router.get("/requests/outgoing", response_model=list[FriendRequestResponse])
def get_outgoing_requests(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    return [
        request_response(db, current_player.player_id, friend_request)
        for friend_request in list_outgoing_requests(db, current_player.player_id)
    ]


@router.post("/requests/{request_id}/accept", response_model=FriendRequestResponse)
async def accept_request(
    http_request: Request,
    request_id: str,
    background_tasks: BackgroundTasks,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        http_request,
        scope="friends-accept-player",
        identifier=current_player.player_id,
        limit=60,
        window_seconds=3600,
    )
    logger.warning(
        "Accepting friend request receiver=%s request_id=%s",
        current_player.player_id,
        request_id,
    )

    try:
        friend_request = accept_friend_request(db, current_player, request_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    set_rls_current_player(db, current_player.player_id)

    sender = get_player_public_profile(db, friend_request.sender_player_id)
    invalidate_social_cache(current_player.player_id, friend_request.sender_player_id)

    if sender:
        tokens = get_active_push_tokens(db, sender.player_id)

        logger.warning(
            "Friend accept push lookup accepter=%s sender=%s notifications_enabled=%s token_count=%s",
            current_player.player_id,
            sender.player_id,
            sender.friend_request_notifications_enabled,
            len(tokens),
        )

        if sender.friend_request_notifications_enabled and tokens:
            background_tasks.add_task(
                send_expo_push_to_tokens,
                tokens,
                "Friend request accepted",
                f"{current_player.player_name} accepted your friend request",
                {
                    "type": "friend_request_accepted",
                    "friend_request_id": friend_request.id,
                    "accepted_by_player_id": current_player.player_id,
                },
            )

    return request_response(db, current_player.player_id, friend_request)


@router.post("/requests/{request_id}/reject", response_model=FriendRequestResponse)
async def reject_request(
    http_request: Request,
    request_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        http_request,
        scope="friends-reject-player",
        identifier=current_player.player_id,
        limit=60,
        window_seconds=3600,
    )
    try:
        friend_request = reject_friend_request(db, current_player, request_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    invalidate_social_cache(current_player.player_id, friend_request.sender_player_id)
    return request_response(db, current_player.player_id, friend_request)


@router.delete("/{friend_player_id}")
def delete_friend(
    friend_player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    removed = remove_friendship(db, current_player.player_id, friend_player_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Friendship not found")
    invalidate_social_cache(current_player.player_id, friend_player_id)
    return {"detail": "Friend removed"}


@router.get("", response_model=FriendsListResponse)
@router.get("/", response_model=FriendsListResponse)
def get_friends(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    cache_key = friends_cache_key(current_player.player_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    response = FriendsListResponse(
        friends=[
            profile_response(db, current_player.player_id, friend, "friends")
            for friend in list_friends(db, current_player.player_id)
        ]
    )
    cache.set(cache_key, response.model_dump(mode="json"), ttl_seconds=45)
    return response


@router.get("/presence", response_model=FriendsPresenceResponse)
async def get_friends_presence(
    request: Request,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="friends-presence-player",
        identifier=current_player.player_id,
        limit=120,
        window_seconds=300,
    )
    cache_key = friends_presence_cache_key(current_player.player_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    friends = list_friends(db, current_player.player_id)
    presence_by_player_id = get_presence_map(
        db, [friend.player_id for friend in friends]
    )
    presence = []

    for friend in friends:
        is_online, last_seen_at = visible_presence_for_player(
            friend, presence_by_player_id.get(friend.player_id)
        )
        presence.append(
            PresenceResponse(
                player_id=friend.player_id,
                is_online=is_online,
                show_online_status=friend.show_online_status,
                last_seen_at=last_seen_at,
            )
        )

    response = FriendsPresenceResponse(presence=presence)
    cache.set(cache_key, response.model_dump(mode="json"), ttl_seconds=10)
    return response
