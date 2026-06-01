"""Shared WebSocket game lifecycle helpers."""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.database.dbCRUD import get_final_scores, update_game_session_ended
from app.schemas.game_state_models import GameSessionState
from app.websockets.manager import SessionPhase, manager

logger = logging.getLogger(__name__)


async def handle_game_end(session_code: str, db: Session) -> bool:
    """Finalize a game session and broadcast the authoritative end state."""
    try:
        success = update_game_session_ended(db, session_code)
        if not success:
            game_state = (
                db.query(GameSessionState)
                .filter(GameSessionState.session_code == session_code)
                .first()
            )
            if not game_state or not game_state.ended_at:
                logger.error(f"Failed to end game session {session_code}")
                return False
            logger.info(f"Game session {session_code} was already ended")
        else:
            game_state = (
                db.query(GameSessionState)
                .filter(GameSessionState.session_code == session_code)
                .first()
            )

        final_scores = get_final_scores(db, session_code)
        ended_at = (
            game_state.ended_at.isoformat()
            if game_state and game_state.ended_at
            else datetime.now().isoformat()
        )
        phase_state = manager.set_session_phase(
            session_code,
            SessionPhase.ENDED,
            ended_at=ended_at,
        )
        manager.clear_question_queue(session_code)
        manager.reset_buzzer_state(session_code)

        logger.info(
            f"Game ended for session {session_code} with {len(final_scores)} players"
        )

        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_ended",
                "data": {
                    "session_code": session_code,
                    "ended_at": ended_at,
                    "phase": phase_state["phase"],
                    "phase_started_at": phase_state["phase_started_at"],
                    "server_time_ms": phase_state["server_time_ms"],
                    "final_scores": final_scores,
                },
            },
            critical=True,
            require_ack=True,
        )

        logger.info(f"Game end broadcast complete for session {session_code}")
        return True

    except Exception as e:
        logger.error(f"Error ending game: {e}", exc_info=True)
        return False
