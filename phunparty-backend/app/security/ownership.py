from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.game_session_model import GameSession
from app.schemas.game_state_models import GameSessionState
from app.schemas.players_model import Players
from app.schemas.session_player_assignment_model import SessionAssignment


def forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to access this resource.",
    )


def not_found(resource: str = "Resource") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource} not found.",
    )


def assert_same_player(current_player: Players, requested_player_id: str) -> None:
    if current_player.player_id != requested_player_id:
        raise forbidden()


def get_session_or_404(db: Session, session_code: str) -> GameSession:
    session = (
        db.query(GameSession)
        .filter(GameSession.session_code == session_code)
        .first()
    )
    if not session:
        raise not_found("Session")
    return session


def is_session_owner(db: Session, current_player: Players, session_code: str) -> bool:
    session = get_session_or_404(db, session_code)
    return session.owner_player_id == current_player.player_id


def assert_session_owner(
    db: Session,
    current_player: Players,
    session_code: str,
) -> GameSession:
    session = get_session_or_404(db, session_code)

    if session.owner_player_id != current_player.player_id:
        raise forbidden()

    return session


def is_session_member(db: Session, current_player: Players, session_code: str) -> bool:
    assignment = (
        db.query(SessionAssignment)
        .filter(SessionAssignment.session_code == session_code)
        .filter(SessionAssignment.player_id == current_player.player_id)
        .filter(SessionAssignment.session_end.is_(None))
        .first()
    )
    return assignment is not None


def assert_session_member_or_owner(
    db: Session,
    current_player: Players,
    session_code: str,
) -> None:
    if is_session_owner(db, current_player, session_code):
        return

    if is_session_member(db, current_player, session_code):
        return

    raise forbidden()


def assert_public_or_member_or_owner(
    db: Session,
    current_player: Players,
    session_code: str,
) -> None:
    if is_session_owner(db, current_player, session_code):
        return

    if is_session_member(db, current_player, session_code):
        return

    state = (
        db.query(GameSessionState)
        .filter(GameSessionState.session_code == session_code)
        .first()
    )

    if state and state.ispublic:
        return

    raise forbidden()
