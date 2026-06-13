from datetime import UTC, datetime
from typing import Optional

from app.database.dbCRUD import generate_unique_friend_code
from app.database.notification_crud import create_notification
from app.schemas.players_model import Players
from app.schemas.social_models import FriendRequest, Friendship
from app.utils.friend_codes import normalize_friend_code
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
REVOKED = "revoked"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ensure_player_friend_code(db: Session, player: Players) -> Players:
    if player.friend_code:
        return player

    player.friend_code = generate_unique_friend_code(db)
    db.commit()
    return player


def get_player_by_friend_code(db: Session, friend_code: str) -> Optional[Players]:
    normalized_code = normalize_friend_code(friend_code)
    if not normalized_code:
        return None

    return (
        db.query(Players)
        .filter(Players.friend_code == normalized_code)
        .filter(Players.allow_friend_code_search == True)
        .filter(Players.is_deleted == False)
        .filter(Players.is_deactivated == False)
        .first()
    )


def canonical_friendship_pair(player_a_id: str, player_b_id: str) -> tuple[str, str]:
    return tuple(sorted([player_a_id, player_b_id]))


def get_friendship(
    db: Session, player_a_id: str, player_b_id: str
) -> Optional[Friendship]:
    player_low_id, player_high_id = canonical_friendship_pair(player_a_id, player_b_id)
    return (
        db.query(Friendship)
        .filter(Friendship.player_low_id == player_low_id)
        .filter(Friendship.player_high_id == player_high_id)
        .first()
    )


def are_friends(db: Session, player_a_id: str, player_b_id: str) -> bool:
    return get_friendship(db, player_a_id, player_b_id) is not None


def get_pending_request_between(
    db: Session, player_a_id: str, player_b_id: str
) -> Optional[FriendRequest]:
    return (
        db.query(FriendRequest)
        .filter(FriendRequest.status == PENDING)
        .filter(
            or_(
                and_(
                    FriendRequest.sender_player_id == player_a_id,
                    FriendRequest.receiver_player_id == player_b_id,
                ),
                and_(
                    FriendRequest.sender_player_id == player_b_id,
                    FriendRequest.receiver_player_id == player_a_id,
                ),
            )
        )
        .first()
    )


def get_relationship_status(
    db: Session, current_player_id: str, other_player_id: str
) -> str:
    if current_player_id == other_player_id:
        return "self"
    if are_friends(db, current_player_id, other_player_id):
        return "friends"

    pending_request = get_pending_request_between(
        db, current_player_id, other_player_id
    )
    if not pending_request:
        return "none"
    if pending_request.sender_player_id == current_player_id:
        return "outgoing_pending"
    return "incoming_pending"


def send_friend_request(
    db: Session,
    sender: Players,
    receiver_friend_code: str,
    message: Optional[str] = None,
) -> FriendRequest:
    ensure_player_friend_code(db, sender)
    receiver = get_player_by_friend_code(db, receiver_friend_code)
    if not receiver:
        raise ValueError("No player found with that friend code")
    if receiver.player_id == sender.player_id:
        raise ValueError("You cannot send a friend request to yourself")
    if not receiver.allow_friend_requests:
        raise ValueError("This player is not accepting friend requests")
    if are_friends(db, sender.player_id, receiver.player_id):
        raise ValueError("You are already friends with this player")
    if get_pending_request_between(db, sender.player_id, receiver.player_id):
        raise ValueError("A pending friend request already exists")

    friend_request = FriendRequest(
        sender_player_id=sender.player_id,
        receiver_player_id=receiver.player_id,
        message=message,
    )
    db.add(friend_request)
    db.flush()

    create_notification(
        db,
        recipient_player_id=receiver.player_id,
        actor_player_id=sender.player_id,
        notification_type="friend_request_received",
        title="New friend request",
        body=f"{sender.player_name} wants to add you as a friend",
        data={"friend_request_id": friend_request.id},
    )
    db.commit()
    return friend_request


def accept_friend_request(
    db: Session, receiver: Players, request_id: str
) -> FriendRequest:
    friend_request = (
        db.query(FriendRequest)
        .filter(FriendRequest.id == request_id)
        .filter(FriendRequest.receiver_player_id == receiver.player_id)
        .filter(FriendRequest.status == PENDING)
        .first()
    )
    if not friend_request:
        raise ValueError("Pending friend request not found")

    friend_request.status = ACCEPTED
    friend_request.responded_at = utc_now()

    player_low_id, player_high_id = canonical_friendship_pair(
        friend_request.sender_player_id, friend_request.receiver_player_id
    )
    friendship = (
        db.query(Friendship)
        .filter(Friendship.player_low_id == player_low_id)
        .filter(Friendship.player_high_id == player_high_id)
        .first()
    )
    if not friendship:
        db.add(
            Friendship(
                player_low_id=player_low_id,
                player_high_id=player_high_id,
            )
        )

    create_notification(
        db,
        recipient_player_id=friend_request.sender_player_id,
        actor_player_id=receiver.player_id,
        notification_type="friend_request_accepted",
        title="Friend request accepted",
        body=f"{receiver.player_name} accepted your friend request",
        data={"friend_request_id": friend_request.id},
    )
    db.commit()
    return friend_request


def reject_friend_request(
    db: Session, receiver: Players, request_id: str
) -> FriendRequest:
    friend_request = (
        db.query(FriendRequest)
        .filter(FriendRequest.id == request_id)
        .filter(FriendRequest.receiver_player_id == receiver.player_id)
        .filter(FriendRequest.status == PENDING)
        .first()
    )
    if not friend_request:
        raise ValueError("Pending friend request not found")

    friend_request.status = REJECTED
    friend_request.responded_at = utc_now()
    db.commit()
    return friend_request


def revoke_pending_friend_requests_for_player(db: Session, player_id: str) -> int:
    requests = (
        db.query(FriendRequest)
        .filter(FriendRequest.status == PENDING)
        .filter(
            or_(
                FriendRequest.sender_player_id == player_id,
                FriendRequest.receiver_player_id == player_id,
            )
        )
        .all()
    )

    now = utc_now()
    for friend_request in requests:
        friend_request.status = REVOKED
        friend_request.responded_at = now

    return len(requests)


def list_incoming_requests(db: Session, player_id: str) -> list[FriendRequest]:
    return (
        db.query(FriendRequest)
        .filter(FriendRequest.receiver_player_id == player_id)
        .filter(FriendRequest.status == PENDING)
        .order_by(FriendRequest.created_at.desc())
        .all()
    )


def list_outgoing_requests(db: Session, player_id: str) -> list[FriendRequest]:
    return (
        db.query(FriendRequest)
        .filter(FriendRequest.sender_player_id == player_id)
        .filter(FriendRequest.status == PENDING)
        .order_by(FriendRequest.created_at.desc())
        .all()
    )


def list_friends(db: Session, player_id: str) -> list[Players]:
    friendships = (
        db.query(Friendship)
        .filter(
            or_(
                Friendship.player_low_id == player_id,
                Friendship.player_high_id == player_id,
            )
        )
        .order_by(Friendship.created_at.desc())
        .all()
    )
    friend_ids = [
        (
            friendship.player_high_id
            if friendship.player_low_id == player_id
            else friendship.player_low_id
        )
        for friendship in friendships
    ]
    if not friend_ids:
        return []

    return (
        db.query(Players)
        .filter(Players.player_id.in_(friend_ids))
        .filter(Players.is_deleted == False)
        .filter(Players.is_deactivated == False)
        .all()
    )


def remove_friendship(db: Session, player_id: str, friend_player_id: str) -> bool:
    friendship = get_friendship(db, player_id, friend_player_id)
    if not friendship:
        return False

    db.delete(friendship)
    db.commit()
    return True


def get_player_public_profile(db: Session, player_id: str) -> Optional[Players]:
    return (
        db.query(Players)
        .filter(Players.player_id == player_id)
        .filter(Players.is_deleted == False)
        .filter(Players.is_deactivated == False)
        .first()
    )
