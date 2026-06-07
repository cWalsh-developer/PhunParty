from app.database.friend_crud import (accept_friend_request,
                                      ensure_player_friend_code,
                                      get_player_by_friend_code,
                                      get_player_public_profile,
                                      get_relationship_status, list_friends,
                                      list_incoming_requests,
                                      list_outgoing_requests,
                                      reject_friend_request, remove_friendship,
                                      send_friend_request)
from app.database.notification_crud import get_active_push_tokens
from app.dependencies import get_current_player, get_db
from app.models.friends import (FriendCodeResponse, FriendProfileResponse,
                                FriendRequestCreate, FriendRequestResponse,
                                FriendSearchRequest, FriendsListResponse)
from app.schemas.players_model import Players
from app.schemas.social_models import FriendRequest
from app.utils.expo_push import send_expo_push_to_tokens
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

router = APIRouter()


def profile_response(
    db: Session,
    current_player_id: str,
    player: Players,
    relationship_status: str | None = None,
) -> FriendProfileResponse:
    ensure_player_friend_code(db, player)
    return FriendProfileResponse(
        player_id=player.player_id,
        player_name=player.player_name,
        profile_photo_url=player.profile_photo_url,
        friend_code=player.friend_code,
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
def search_by_friend_code(
    request: FriendSearchRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    current_player = ensure_player_friend_code(db, current_player)
    player = get_player_by_friend_code(db, request.friend_code)
    if not player:
        raise HTTPException(status_code=404, detail="No player found with that code")
    return profile_response(db, current_player.player_id, player)


@router.post("/requests", response_model=FriendRequestResponse)
async def create_friend_request(
    request: FriendRequestCreate,
    background_tasks: BackgroundTasks,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    try:
        friend_request = send_friend_request(
            db, current_player, request.friend_code, request.message
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    receiver = get_player_public_profile(db, friend_request.receiver_player_id)
    if receiver and receiver.friend_request_notifications_enabled:
        tokens = get_active_push_tokens(db, receiver.player_id)
        if tokens:
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
    request_id: str,
    background_tasks: BackgroundTasks,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    try:
        friend_request = accept_friend_request(db, current_player, request_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    sender = get_player_public_profile(db, friend_request.sender_player_id)
    if sender and sender.friend_request_notifications_enabled:
        tokens = get_active_push_tokens(db, sender.player_id)
        if tokens:
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
def reject_request(
    request_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    try:
        friend_request = reject_friend_request(db, current_player, request_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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
    return {"detail": "Friend removed"}


@router.get("", response_model=FriendsListResponse)
@router.get("/", response_model=FriendsListResponse)
def get_friends(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    friends = [
        profile_response(db, current_player.player_id, friend, "friends")
        for friend in list_friends(db, current_player.player_id)
    ]
    return FriendsListResponse(friends=friends)
