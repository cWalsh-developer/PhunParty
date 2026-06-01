"""Server-owned scheduling helpers for synchronized question reveals."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.database.dbCRUD import get_current_question_details
from app.dependencies import get_db
from app.websockets.manager import SessionPhase, manager

logger = logging.getLogger(__name__)

COUNTDOWN_DURATION_MS = 3000
QUESTION_BROADCAST_LEAD_MS = 500


def iso_utc(dt: datetime) -> str:
    return dt.isoformat() + "Z"


async def reveal_current_question(
    session_code: str, db: Session, start_at_iso: str
) -> bool:
    """Move the server-owned phase to QUESTION and broadcast exactly once."""
    if manager.get_session_phase_state(session_code).get("phase") == "question":
        logger.info(f"Question already revealed for session {session_code}; skipping")
        return False

    game_status = get_current_question_details(db, session_code)
    if not game_status or not game_status.get("current_question"):
        logger.warning(f"No current question found for session {session_code}")
        return False

    question_data = game_status["current_question"]
    question_data["start_at"] = start_at_iso
    phase_state = manager.set_session_phase(
        session_code,
        SessionPhase.QUESTION,
        start_at=start_at_iso,
        current_question_id=question_data.get("question_id"),
        current_question_index=game_status.get("current_question_index"),
        total_questions=game_status.get("total_questions"),
    )
    question_data["phase"] = phase_state["phase"]
    question_data["server_time_ms"] = phase_state["server_time_ms"]

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
    return True


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
    duration_ms: int = COUNTDOWN_DURATION_MS,
    delay_ms: int = 250,
    reason: str = "intro_complete",
    current_question_id: Optional[str] = None,
    current_question_index: Optional[int] = None,
    total_questions: Optional[int] = None,
):
    """Enter COUNTDOWN and schedule the authoritative QUESTION transition."""
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
