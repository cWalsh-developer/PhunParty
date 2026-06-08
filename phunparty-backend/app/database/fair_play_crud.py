from datetime import UTC, datetime
from typing import Optional

from app.database.dbCRUD import get_game_session_state
from app.schemas.fair_play_models import FairPlayViolation, SessionPlayerFairPlay
from app.schemas.game_state_models import PlayerResponse
from app.schemas.scores_model import Scores
from app.schemas.session_player_assignment_model import SessionAssignment
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

DEFAULT_MAX_FAIR_PLAY_STRIKES = 3


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def update_fair_play_settings(
    db: Session,
    session_code: str,
    fair_play_enabled: Optional[bool] = None,
    max_fair_play_strikes: Optional[int] = None,
):
    game_state = get_game_session_state(db, session_code)
    if not game_state:
        raise ValueError("Game session not found")

    if fair_play_enabled is not None:
        game_state.fair_play_enabled = fair_play_enabled
    if max_fair_play_strikes is not None:
        game_state.max_fair_play_strikes = max(1, int(max_fair_play_strikes))

    db.commit()
    return game_state


def get_fair_play_record(
    db: Session, session_code: str, player_id: str
) -> Optional[SessionPlayerFairPlay]:
    return (
        db.query(SessionPlayerFairPlay)
        .filter(SessionPlayerFairPlay.session_code == session_code)
        .filter(SessionPlayerFairPlay.player_id == player_id)
        .first()
    )


def get_or_create_fair_play_record(
    db: Session, session_code: str, player_id: str
) -> SessionPlayerFairPlay:
    record = get_fair_play_record(db, session_code, player_id)
    if record:
        return record

    record = SessionPlayerFairPlay(session_code=session_code, player_id=player_id)
    db.add(record)
    db.flush()
    return record


def has_focus_violation_for_question(
    db: Session, session_code: str, player_id: str, question_id: str
) -> bool:
    return (
        db.query(FairPlayViolation)
        .filter(FairPlayViolation.session_code == session_code)
        .filter(FairPlayViolation.player_id == player_id)
        .filter(FairPlayViolation.question_id == question_id)
        .first()
        is not None
    )


def parse_occurred_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


def void_player_response_for_question(
    db: Session, session_code: str, player_id: str, question_id: str
) -> bool:
    response = (
        db.query(PlayerResponse)
        .filter(PlayerResponse.session_code == session_code)
        .filter(PlayerResponse.player_id == player_id)
        .filter(PlayerResponse.question_id == question_id)
        .first()
    )
    if not response:
        return False

    if response.is_correct:
        score = (
            db.query(Scores)
            .filter(Scores.session_code == session_code)
            .filter(Scores.player_id == player_id)
            .first()
        )
        if score and score.score > 0:
            score.score -= 1

    db.delete(response)
    return True


def record_focus_violation(
    db: Session,
    session_code: str,
    player_id: str,
    question_id: str,
    reason: Optional[str] = None,
    occurred_at: Optional[str] = None,
    max_strikes: int = DEFAULT_MAX_FAIR_PLAY_STRIKES,
) -> tuple[SessionPlayerFairPlay, FairPlayViolation, bool]:
    if has_focus_violation_for_question(db, session_code, player_id, question_id):
        raise ValueError("Focus violation already recorded for this question")

    record = get_or_create_fair_play_record(db, session_code, player_id)
    violation = FairPlayViolation(
        session_code=session_code,
        player_id=player_id,
        question_id=question_id,
        reason=reason,
        occurred_at=parse_occurred_at(occurred_at),
    )
    db.add(violation)
    response_voided = void_player_response_for_question(
        db, session_code, player_id, question_id
    )

    record.strike_count += 1
    record.updated_at = utc_now()
    if record.strike_count >= max_strikes:
        record.is_kicked = True

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Focus violation already recorded for this question")
    return record, violation, response_voided


def is_player_kicked(db: Session, session_code: str, player_id: str) -> bool:
    record = get_fair_play_record(db, session_code, player_id)
    return bool(record and record.is_kicked)


def is_player_frozen_for_question(
    db: Session, session_code: str, player_id: str, question_id: str
) -> bool:
    return has_focus_violation_for_question(db, session_code, player_id, question_id)


def count_fair_play_resolved_players_for_question(
    db: Session, session_code: str, question_id: str
) -> int:
    """Count players resolved by Fair Play violations without double-counting answers."""
    answered_player_ids = (
        db.query(PlayerResponse.player_id)
        .filter(PlayerResponse.session_code == session_code)
        .filter(PlayerResponse.question_id == question_id)
    )
    kicked_player_ids = (
        db.query(SessionPlayerFairPlay.player_id)
        .filter(SessionPlayerFairPlay.session_code == session_code)
        .filter(SessionPlayerFairPlay.is_kicked.is_(True))
    )
    return (
        db.query(FairPlayViolation.player_id)
        .filter(FairPlayViolation.session_code == session_code)
        .filter(FairPlayViolation.question_id == question_id)
        .filter(FairPlayViolation.player_id.notin_(answered_player_ids))
        .filter(FairPlayViolation.player_id.notin_(kicked_player_ids))
        .distinct()
        .count()
    )


def count_kicked_players(db: Session, session_code: str) -> int:
    return (
        db.query(SessionPlayerFairPlay.player_id)
        .filter(SessionPlayerFairPlay.session_code == session_code)
        .filter(SessionPlayerFairPlay.is_kicked.is_(True))
        .distinct()
        .count()
    )


def get_eligible_player_ids_for_session(db: Session, session_code: str) -> list[str]:
    kicked_player_ids = (
        db.query(SessionPlayerFairPlay.player_id)
        .filter(SessionPlayerFairPlay.session_code == session_code)
        .filter(SessionPlayerFairPlay.is_kicked.is_(True))
    )
    return [
        row[0]
        for row in (
            db.query(SessionAssignment.player_id)
            .filter(SessionAssignment.session_code == session_code)
            .filter(SessionAssignment.player_id.notin_(kicked_player_ids))
            .distinct()
            .all()
        )
    ]
