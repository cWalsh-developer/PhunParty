"""Shared WebSocket game lifecycle helpers."""

import asyncio
import inspect
import logging
from datetime import datetime

from app.database.dbCRUD import (
    get_final_scores,
    update_game_session_ended,
    get_session_by_code,
)
from app.schemas.game_state_models import GameSessionState
from app.websockets.manager import SessionPhase, manager
from app.security.rls import set_rls_current_player
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


async def handle_game_end(
    session_code: str,
    db: Session,
    acting_player_id: str | None = None,
) -> bool:
    """Finalize a game session and broadcast the authoritative end state."""
    try:
        # Ensure this DB session has an RLS context before reading/updating
        # game_session_states, scores, assignments, etc.
        if acting_player_id:
            set_rls_current_player(db, acting_player_id)
        else:
            session = get_session_by_code(db, session_code)
            owner_player_id = (
                getattr(session, "owner_player_id", None) if session else None
            )

            if owner_player_id:
                set_rls_current_player(db, owner_player_id)
            else:
                logger.warning(
                    "Could not resolve owner RLS context while ending session=%s",
                    session_code,
                )

        success = update_game_session_ended(db, session_code)

        # Important: query by session_code directly here, not through
        # get_game_session_state(), because get_game_session_state() only returns
        # active sessions. Once a game is ended, is_active may already be false.
        game_state = (
            db.query(GameSessionState)
            .filter(GameSessionState.session_code == session_code)
            .first()
        )

        if not success:
            if not game_state:
                logger.error(
                    "Failed to end game session %s; no game state found", session_code
                )
                return False

            if not game_state.ended_at:
                logger.error(
                    "Failed to end game session %s; game state exists but ended_at is missing",
                    session_code,
                )
                return False

            logger.info("Game session %s was already ended", session_code)

        final_scores = get_final_scores(db, session_code)

        fair_play_statuses = manager.fair_play_player_status.get(session_code, {})
        removed_players = [
            {
                "player_id": player_id,
                **status,
            }
            for player_id, status in fair_play_statuses.items()
            if status.get("is_kicked") is True
        ]

        ended_at = (
            game_state.ended_at.isoformat()
            if game_state and game_state.ended_at
            else datetime.now().isoformat()
        )

        terminal_snapshot = {
            "phase": SessionPhase.ENDED.value,
            "ended_at": ended_at,
            "final_scores": final_scores,
            "fair_play_player_status": {
                player_id: {
                    "player_id": player_id,
                    **status,
                }
                for player_id, status in fair_play_statuses.items()
            },
            "removed_players": removed_players,
            "kicked_players": removed_players,
        }

        manager.remember_terminal_session(
            session_code,
            terminal_snapshot,
            ttl_seconds=900,
        )

        phase_state = manager.set_session_phase(
            session_code,
            SessionPhase.ENDED,
            ended_at=ended_at,
        )

        manager.clear_question_queue(session_code)
        manager.reset_buzzer_state(session_code)

        logger.info(
            "Game ended for session %s with %s final scores",
            session_code,
            len(final_scores),
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
                    "removed_players": removed_players,
                    "kicked_players": removed_players,
                },
            },
            critical=True,
            require_ack=True,
        )

        logger.info("Game end broadcast complete for session %s", session_code)

        cleanup_task = manager.cleanup_session_later(session_code, delay_seconds=60)
        if inspect.isawaitable(cleanup_task):
            asyncio.create_task(cleanup_task)

        terminal_cleanup_task = manager.cleanup_terminal_session_later(
            session_code,
            delay_seconds=900,
        )
        if inspect.isawaitable(terminal_cleanup_task):
            asyncio.create_task(terminal_cleanup_task)

        return True

    except Exception:
        logger.exception("Error ending game for session %s", session_code)
        return False
