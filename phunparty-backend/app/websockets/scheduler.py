"""Server-owned scheduling helpers for synchronized question reveals."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.database.dbCRUD import advance_to_next_question, get_current_question_details
from app.dependencies import get_db
from app.websockets.manager import SessionPhase, manager

logger = logging.getLogger(__name__)

COUNTDOWN_DURATION_MS = 3000
NEXT_QUESTION_REVEAL_DELAY_MS = 250
QUESTION_BROADCAST_LEAD_MS = 500
QUESTION_DURATION_MS = 15000


def iso_utc(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def parse_iso_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", ""))


def normalize_countdown_duration_ms(
    duration_ms: Optional[int], session_code: str = "", reason: str = ""
) -> int:
    """Keep countdown timing server-owned even if a client sends bad duration data."""
    try:
        parsed_duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        parsed_duration_ms = None

    if parsed_duration_ms != COUNTDOWN_DURATION_MS:
        logger.warning(
            "Ignoring client countdown duration for session %s reason=%s: %s; using %sms",
            session_code,
            reason,
            duration_ms,
            COUNTDOWN_DURATION_MS,
        )

    return COUNTDOWN_DURATION_MS


async def advance_or_end_current_question(
    session_code: str, db: Session, reason: str = "question_timeout"
) -> bool:
    """Advance the DB question pointer and broadcast the next authoritative state."""
    result = advance_to_next_question(db, session_code)
    action = result.get("action")

    if action == "next_question":
        game_status = get_current_question_details(db, session_code)
        current_question = game_status.get("current_question") if game_status else None
        if not current_question:
            logger.warning(
                f"Could not reveal next question for {session_code}; no current question after {reason}"
            )
            return False

        manager.clear_question_queue(session_code)
        question_start_at = datetime.utcnow() + timedelta(
            milliseconds=NEXT_QUESTION_REVEAL_DELAY_MS
        )
        return await reveal_current_question(session_code, db, iso_utc(question_start_at))

    if action == "game_ended":
        from app.websockets.game_lifecycle import handle_game_end

        return await handle_game_end(session_code, db)

    logger.warning(
        f"Unexpected advance result for {session_code} after {reason}: {result}"
    )
    return False


async def reveal_current_question(
    session_code: str, db: Session, start_at_iso: str
) -> bool:
    """Move the server-owned phase to QUESTION and broadcast exactly once."""
    game_status = get_current_question_details(db, session_code)
    if not game_status or not game_status.get("current_question"):
        logger.warning(f"No current question found for session {session_code}")
        return False

    question_data = game_status["current_question"]
    question_id = question_data.get("question_id")
    phase_state = manager.get_session_phase_state(session_code)
    if (
        phase_state.get("phase") == SessionPhase.QUESTION.value
        and question_id
        and phase_state.get("current_question_id") == question_id
    ):
        logger.info(
            f"Question {question_id} already revealed for session {session_code}; skipping"
        )
        return False

    try:
        question_starts_at = parse_iso_utc(start_at_iso)
    except ValueError:
        logger.warning(
            f"Invalid question start_at for {session_code}: {start_at_iso}; using current time"
        )
        question_starts_at = datetime.utcnow()

    expires_at = question_starts_at + timedelta(milliseconds=QUESTION_DURATION_MS)
    expires_at_iso = iso_utc(expires_at)
    question_data["start_at"] = start_at_iso
    question_data["expires_at"] = expires_at_iso
    question_data["duration_ms"] = QUESTION_DURATION_MS
    phase_state = manager.set_session_phase(
        session_code,
        SessionPhase.QUESTION,
        start_at=start_at_iso,
        question_expires_at=expires_at_iso,
        question_duration_ms=QUESTION_DURATION_MS,
        current_question_id=question_data.get("question_id"),
        current_question_index=game_status.get("current_question_index"),
        total_questions=game_status.get("total_questions"),
    )
    question_data["phase"] = phase_state["phase"]
    question_data["server_time_ms"] = phase_state["server_time_ms"]

    manager.reset_all_players_answered(session_code)
    manager.start_buzzer_question(session_code, question_id)
    manager.queue_question(session_code, question_data)
    await manager.broadcast_to_session(
        session_code,
        {
            "type": "question_started",
            "data": question_data,
        },
        critical=True,
        require_ack=True,
    )
    logger.info(
        f"Question {question_data.get('question_id')} scheduled for session {session_code} at {start_at_iso}"
    )
    if question_id:
        asyncio.create_task(
            scheduled_question_timeout(session_code, question_id, expires_at)
        )
    return True


async def scheduled_question_timeout(
    session_code: str, question_id: str, expires_at: datetime
):
    """Advance the current question when the server-owned question timer expires."""
    sleep_seconds = max(0, (expires_at - datetime.utcnow()).total_seconds())
    await asyncio.sleep(sleep_seconds)

    phase_state = manager.get_session_phase_state(session_code)
    if phase_state.get("phase") != SessionPhase.QUESTION.value:
        logger.info(
            f"Skipping question timeout for {session_code}; phase is {phase_state.get('phase')}"
        )
        return

    if phase_state.get("current_question_id") != question_id:
        logger.info(
            f"Skipping stale question timeout for {session_code}; current question is {phase_state.get('current_question_id')}"
        )
        return

    expected_expires_at = phase_state.get("question_expires_at")
    expires_at_iso = iso_utc(expires_at)
    if expected_expires_at and expected_expires_at != expires_at_iso:
        logger.info(
            f"Skipping stale question timeout for {session_code}; expected {expected_expires_at}"
        )
        return

    db_generator = get_db()
    db = next(db_generator)
    try:
        logger.info(f"Question {question_id} timed out for session {session_code}")
        await advance_or_end_current_question(
            session_code, db, reason="question_timeout"
        )
    finally:
        db_generator.close()


async def scheduled_question_reveal(session_code: str, question_start_at: datetime):
    """Reveal the current question from a server-owned countdown timer."""
    broadcast_at = question_start_at - timedelta(milliseconds=QUESTION_BROADCAST_LEAD_MS)
    sleep_seconds = max(0, (broadcast_at - datetime.utcnow()).total_seconds())
    await asyncio.sleep(sleep_seconds)

    phase_state = manager.get_session_phase_state(session_code)
    if phase_state.get("phase") != SessionPhase.COUNTDOWN.value:
        logger.info(
            f"Skipping scheduled reveal for {session_code}; phase is {phase_state.get('phase')}"
        )
        return

    expected_start_at = phase_state.get("question_start_at")
    question_start_at_iso = iso_utc(question_start_at)
    if expected_start_at and expected_start_at != question_start_at_iso:
        logger.info(
            f"Skipping stale scheduled reveal for {session_code}; expected {expected_start_at}"
        )
        return

    db_generator = get_db()
    db = next(db_generator)
    try:
        await reveal_current_question(session_code, db, question_start_at_iso)
    finally:
        db_generator.close()


async def start_countdown(
    session_code: str,
    duration_ms: Optional[int] = COUNTDOWN_DURATION_MS,
    delay_ms: int = 250,
    reason: str = "intro_complete",
    current_question_id: Optional[str] = None,
    current_question_index: Optional[int] = None,
    total_questions: Optional[int] = None,
):
    """Enter COUNTDOWN and schedule the authoritative QUESTION transition."""
    duration_ms = normalize_countdown_duration_ms(duration_ms, session_code, reason)
    countdown_start = datetime.utcnow() + timedelta(milliseconds=delay_ms)
    question_start_at = countdown_start + timedelta(milliseconds=duration_ms)
    countdown_start_iso = iso_utc(countdown_start)
    question_start_at_iso = iso_utc(question_start_at)

    phase_state = manager.set_session_phase(
        session_code,
        SessionPhase.COUNTDOWN,
        start_at=countdown_start_iso,
        duration_ms=duration_ms,
        question_start_at=question_start_at_iso,
        countdown_reason=reason,
        current_question_id=current_question_id,
        current_question_index=current_question_index,
        total_questions=total_questions,
    )

    await manager.broadcast_to_session(
        session_code,
        {
            "type": "countdown_started",
            "data": {
                **phase_state,
                "start_at": countdown_start_iso,
                "duration_ms": duration_ms,
                "question_start_at": question_start_at_iso,
            },
        },
        critical=True,
        require_ack=True,
    )
    asyncio.create_task(scheduled_question_reveal(session_code, question_start_at))
