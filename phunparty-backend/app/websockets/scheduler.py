"""Server-owned scheduling helpers for synchronized question reveals."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from app.database.dbCRUD import (
    advance_to_next_question,
    get_current_question_details,
    get_game_session_state,
    get_session_by_code,
)
from app.dependencies import get_db
from app.security.question_payload import sanitize_question_for_client
from app.security.rls import set_rls_current_player
from app.websockets.game_modes import BUZZER_GAME_TYPE, resolve_session_game_type
from app.websockets.manager import SessionPhase, manager
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

COUNTDOWN_DURATION_MS = 3000
NEXT_QUESTION_REVEAL_DELAY_MS = 2000
QUESTION_BROADCAST_LEAD_MS = 2000
QUESTION_DURATION_MS = 30000
TIMED_QUESTION_DIFFICULTIES = {"medium", "hard"}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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


def format_buzzer_question_for_mobile(question_data: dict) -> dict:
    """Return the mobile buzzer UI payload for a revealed question."""
    return {
        "game_type": BUZZER_GAME_TYPE,
        "question_id": question_data.get("question_id"),
        "start_at": question_data.get("start_at"),
        "expires_at": question_data.get("expires_at"),
        "duration_ms": question_data.get("duration_ms"),
        "phase": question_data.get("phase"),
        "server_time_ms": question_data.get("server_time_ms"),
        "ui_mode": "buzzer",
        "button_state": "active",
        "message": "Press to buzz in!",
    }


def normalize_question_difficulty(question_data: dict) -> str:
    difficulty = question_data.get("difficulty")
    if hasattr(difficulty, "value"):
        difficulty = difficulty.value
    return str(difficulty or "easy").lower()


def question_uses_timer(question_data: dict) -> bool:
    return normalize_question_difficulty(question_data) in TIMED_QUESTION_DIFFICULTIES


def get_session_owner_id(db: Session, session_code: str) -> Optional[str]:
    try:
        session = get_session_by_code(db, session_code)
        return getattr(session, "owner_player_id", None) if session else None
    except Exception:
        logger.exception("Could not resolve session owner for %s", session_code)
        return None


def apply_scheduled_rls_context(
    db: Session, session_code: str, acting_player_id: Optional[str]
) -> None:
    if acting_player_id:
        set_rls_current_player(db, acting_player_id)
        return

    owner_player_id = get_session_owner_id(db, session_code)
    if owner_player_id:
        set_rls_current_player(db, owner_player_id)
    else:
        logger.warning("Scheduled task has no RLS player context for %s", session_code)


async def advance_or_end_current_question(
    session_code: str,
    db: Session,
    reason: str = "question_timeout",
    acting_player_id: Optional[str] = None,
) -> bool:
    """Advance the DB question pointer and broadcast the next authoritative state."""

    owner_player_id = get_session_owner_id(db, session_code)
    progression_actor_id = owner_player_id or acting_player_id

    if progression_actor_id:
        logger.warning(
            "ADVANCE RLS CONTEXT session=%s reason=%s acting_player=%s owner=%s using=%s",
            session_code,
            reason,
            acting_player_id,
            owner_player_id,
            progression_actor_id,
        )
        set_rls_current_player(db, progression_actor_id)
    else:
        logger.warning(
            "ADVANCE RLS CONTEXT MISSING session=%s reason=%s acting_player=%s",
            session_code,
            reason,
            acting_player_id,
        )

    # CRITICAL:
    # submit_player_answer() commits and changes RLS context before this function
    # is called. The SQLAlchemy Session can still hold an old game_session_states
    # object in its identity map. Expire it BEFORE reading or advancing.
    try:
        db.expire_all()
    except Exception:
        logger.exception(
            "ADVANCE PRE-REFRESH FAILED session=%s reason=%s",
            session_code,
            reason,
        )

    before_state = get_game_session_state(db, session_code)
    logger.warning(
        "ADVANCE BEFORE session=%s reason=%s index=%s current_question=%s total=%s",
        session_code,
        reason,
        getattr(before_state, "current_question_index", None),
        getattr(before_state, "current_question_id", None),
        getattr(before_state, "total_questions", None),
    )

    result = advance_to_next_question(db, session_code)
    action = result.get("action")

    logger.warning(
        "ADVANCE RESULT session=%s reason=%s action=%s result=%s",
        session_code,
        reason,
        action,
        result,
    )

    try:
        db.flush()
        db.expire_all()
    except Exception:
        logger.exception(
            "ADVANCE POST-REFRESH FAILED session=%s reason=%s",
            session_code,
            reason,
        )

    after_state = get_game_session_state(db, session_code)
    logger.warning(
        "ADVANCE AFTER session=%s reason=%s index=%s current_question=%s total=%s",
        session_code,
        reason,
        getattr(after_state, "current_question_index", None),
        getattr(after_state, "current_question_id", None),
        getattr(after_state, "total_questions", None),
    )

    if action == "next_question":
        manager.clear_question_queue(session_code)

        question_start_at = utc_now() + timedelta(
            milliseconds=NEXT_QUESTION_REVEAL_DELAY_MS
        )

        revealed = await reveal_current_question(
            session_code,
            db,
            iso_utc(question_start_at),
            acting_player_id=progression_actor_id,
        )

        logger.warning(
            "ADVANCE REVEAL RESULT session=%s reason=%s next_question=%s revealed=%s",
            session_code,
            reason,
            result.get("next_question_id"),
            revealed,
        )

        return revealed

    if action == "game_ended":
        from app.websockets.game_lifecycle import handle_game_end

        logger.warning(
            "ADVANCE ENDING GAME session=%s reason=%s result=%s",
            session_code,
            reason,
            result,
        )

        return await handle_game_end(
            session_code,
            db,
            acting_player_id=progression_actor_id,
        )

    logger.warning(
        "ADVANCE UNEXPECTED RESULT session=%s reason=%s result=%s",
        session_code,
        reason,
        result,
    )
    return False


async def reveal_current_question(
    session_code: str,
    db: Session,
    start_at_iso: str,
    acting_player_id: Optional[str] = None,
) -> bool:
    """Move the server-owned phase to QUESTION and broadcast exactly once."""
    if acting_player_id:
        set_rls_current_player(db, acting_player_id)

    game_status = get_current_question_details(db, session_code)
    if not game_status or not game_status.get("current_question"):
        game_state = get_game_session_state(db, session_code)
        logger.warning(
            "No current question found for session %s while revealing. "
            "game_status=%s game_state_exists=%s current_question_id=%s acting_player_id=%s",
            session_code,
            game_status,
            bool(game_state),
            getattr(game_state, "current_question_id", None),
            acting_player_id,
        )
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "error",
                "data": {
                    "message": "Could not reveal the current question.",
                    "reason": "current_question_not_visible",
                },
            },
            only_client_types=["web"],
        )
        return False

    question_data = game_status["current_question"]
    game_type = resolve_session_game_type(db, session_code)
    logger.warning(
        "REVEAL CURRENT QUESTION session=%s question_id=%s index=%s total=%s game_type=%s",
        session_code,
        question_data.get("question_id") if question_data else None,
        game_status.get("current_question_index") if game_status else None,
        game_status.get("total_questions") if game_status else None,
        game_type,
    )
    scheduled_player_id = acting_player_id or get_session_owner_id(db, session_code)
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
        question_starts_at = utc_now()

    has_question_timer = question_uses_timer(question_data)
    expires_at = (
        question_starts_at + timedelta(milliseconds=QUESTION_DURATION_MS)
        if has_question_timer
        else None
    )
    expires_at_iso = iso_utc(expires_at) if expires_at else None
    question_data["start_at"] = start_at_iso
    if has_question_timer:
        question_data["expires_at"] = expires_at_iso
        question_data["duration_ms"] = QUESTION_DURATION_MS
    else:
        question_data.pop("expires_at", None)
        question_data.pop("duration_ms", None)
    phase_state = manager.set_session_phase(
        session_code,
        SessionPhase.QUESTION,
        start_at=start_at_iso,
        question_expires_at=expires_at_iso,
        question_duration_ms=QUESTION_DURATION_MS if has_question_timer else None,
        clear_fields=(
            ["question_expires_at", "question_duration_ms"]
            if not has_question_timer
            else None
        ),
        current_question_id=question_data.get("question_id"),
        current_question_index=game_status.get("current_question_index"),
        total_questions=game_status.get("total_questions"),
    )
    question_data["phase"] = phase_state["phase"]
    question_data["server_time_ms"] = phase_state["server_time_ms"]
    question_data["game_type"] = game_type
    client_question_data = sanitize_question_for_client(question_data)

    manager.reset_all_players_answered(session_code)
    if question_id:
        manager.reset_fair_play_freezes_for_question(session_code, question_id)
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "fair_play_question_reset",
                "data": {
                    "question_id": question_id,
                    "is_frozen": False,
                    "frozen_question_id": None,
                    "answer_status": None,
                },
            },
            only_client_types=["mobile"],
            critical=True,
        )
    if game_type == BUZZER_GAME_TYPE:
        manager.start_buzzer_question(session_code, question_id)
        mobile_question_data = format_buzzer_question_for_mobile(client_question_data)
        manager.queue_question(session_code, mobile_question_data)
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "question_started",
                "data": client_question_data,
            },
            only_client_types=["web"],
            critical=True,
            require_ack=True,
        )
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "question_started",
                "data": mobile_question_data,
            },
            only_client_types=["mobile"],
            critical=True,
            require_ack=True,
        )
        await manager.broadcast_buzzer_state_update(session_code)
    else:
        manager.reset_buzzer_state(session_code)
        manager.queue_question(session_code, client_question_data)
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "question_started",
                "data": client_question_data,
            },
            critical=True,
            require_ack=True,
        )
    logger.info(
        f"Question {question_data.get('question_id')} scheduled for session {session_code} at {start_at_iso}"
    )
    if question_id and expires_at:
        asyncio.create_task(
            scheduled_question_timeout(
                session_code,
                question_id,
                expires_at,
                acting_player_id=scheduled_player_id,
            )
        )
    if question_id:
        try:
            game_state = get_game_session_state(db, session_code)
            if game_state and getattr(game_state, "fair_play_enabled", False) is True:
                from app.websockets.routes import (
                    schedule_absent_player_fair_play_checks,
                )

                asyncio.create_task(
                    schedule_absent_player_fair_play_checks(
                        session_code=session_code,
                        question_id=question_id,
                        acting_player_id=scheduled_player_id,
                    )
                )
        except Exception:
            logger.exception(
                f"Could not schedule absent Fair Play checks for {session_code}"
            )
    return True


async def scheduled_question_timeout(
    session_code: str,
    question_id: str,
    expires_at: datetime,
    acting_player_id: Optional[str] = None,
):
    """Advance the current question when the server-owned question timer expires."""
    sleep_seconds = max(0, (expires_at - utc_now()).total_seconds())
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
        apply_scheduled_rls_context(db, session_code, acting_player_id)
        logger.info(f"Question {question_id} timed out for session {session_code}")
        await advance_or_end_current_question(
            session_code,
            db,
            reason="question_timeout",
            acting_player_id=acting_player_id,
        )
    finally:
        db_generator.close()


async def scheduled_question_reveal(
    session_code: str,
    question_start_at: datetime,
    acting_player_id: Optional[str] = None,
):
    """Reveal the current question from a server-owned countdown timer."""
    broadcast_at = question_start_at - timedelta(
        milliseconds=QUESTION_BROADCAST_LEAD_MS
    )
    sleep_seconds = max(0, (broadcast_at - utc_now()).total_seconds())
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
        apply_scheduled_rls_context(db, session_code, acting_player_id)
        await reveal_current_question(
            session_code,
            db,
            question_start_at_iso,
            acting_player_id=acting_player_id,
        )
    except Exception:
        logger.exception(
            "Scheduled question reveal failed for session %s", session_code
        )
    finally:
        db_generator.close()


async def scheduled_countdown_watchdog(
    session_code: str,
    question_start_at: datetime,
    acting_player_id: Optional[str] = None,
):
    """Reveal the question if the countdown scheduler left the session behind."""
    sleep_seconds = max(
        0,
        (question_start_at + timedelta(milliseconds=750) - utc_now()).total_seconds(),
    )
    await asyncio.sleep(sleep_seconds)

    phase_state = manager.get_session_phase_state(session_code)
    question_start_at_iso = iso_utc(question_start_at)
    if (
        phase_state.get("phase") != SessionPhase.COUNTDOWN.value
        or phase_state.get("question_start_at") != question_start_at_iso
    ):
        return

    logger.warning(
        "Countdown watchdog revealing stuck session %s at %s",
        session_code,
        question_start_at_iso,
    )

    db_generator = get_db()
    db = next(db_generator)
    try:
        apply_scheduled_rls_context(db, session_code, acting_player_id)
        await reveal_current_question(
            session_code,
            db,
            question_start_at_iso,
            acting_player_id=acting_player_id,
        )
    except Exception:
        logger.exception("Countdown watchdog failed for session %s", session_code)
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
    acting_player_id: Optional[str] = None,
):
    """Enter COUNTDOWN and schedule the authoritative QUESTION transition."""
    duration_ms = normalize_countdown_duration_ms(duration_ms, session_code, reason)
    countdown_start = utc_now() + timedelta(milliseconds=delay_ms)
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
    asyncio.create_task(
        scheduled_question_reveal(
            session_code,
            question_start_at,
            acting_player_id=acting_player_id,
        )
    )
    asyncio.create_task(
        scheduled_countdown_watchdog(
            session_code,
            question_start_at,
            acting_player_id=acting_player_id,
        )
    )
