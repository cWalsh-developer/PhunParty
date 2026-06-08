"""
Game event handlers for different game types
Handles the business logic for different game modes
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.database.dbCRUD import (
    get_current_question_details,
    get_player_by_ID,
    get_question_by_id,
)
from app.database.fair_play_crud import is_player_frozen_for_question, is_player_kicked
from app.logic.answer_validation import validate_answer_against_question
from app.security.loggingUtils import safe_player_ref
from app.security.question_payload import sanitize_question_for_client
from app.security.roster_identity import make_roster_player_id
from app.websockets.game_lifecycle import handle_game_end
from app.websockets.manager import SessionPhase, manager
from app.websockets.scheduler import (
    NEXT_QUESTION_REVEAL_DELAY_MS,
    advance_or_end_current_question,
    iso_utc,
    reveal_current_question,
    utc_now,
)
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class GameEventHandler:
    """Base class for game event handling"""

    def __init__(self, session_code: str, game_type: str):
        self.session_code = session_code
        self.game_type = game_type
        self.game_state = {}

    async def handle_player_answer(
        self, player_id: str, answer: str, question_id: str, db: Session
    ):
        """Handle player submitting an answer - override in subclasses"""
        raise NotImplementedError

    async def handle_game_start(self, db: Session):
        """Handle game starting - override in subclasses"""
        raise NotImplementedError

    async def broadcast_question(self, question_data: Dict[str, Any]):
        """Broadcast question to all clients with different formats"""
        # Reset all players' answered status for the new question
        manager.reset_all_players_answered(self.session_code)
        start_at = iso_utc(utc_now())
        phase_state = manager.set_session_phase(
            self.session_code,
            SessionPhase.QUESTION,
            start_at=start_at,
            current_question_id=question_data.get("question_id"),
        )
        question_data["start_at"] = start_at
        question_data["phase"] = phase_state["phase"]
        question_data["server_time_ms"] = phase_state["server_time_ms"]
        client_question_data = sanitize_question_for_client(question_data)

        logger.info(f"Broadcasting question to session {self.session_code}")

        # Send sanitized question to web clients with critical flag.
        await manager.broadcast_to_session(
            self.session_code,
            {
                "type": "question_started",
                "data": {
                    "question": client_question_data,
                    "game_type": self.game_type,
                },
            },
            only_client_types=["web"],
            critical=True,
            require_ack=True,
        )

        # Send appropriate mobile UI data to mobile clients with critical flag
        mobile_data = self.format_question_for_mobile(client_question_data)
        await manager.broadcast_to_session(
            self.session_code,
            {"type": "question_started", "data": mobile_data},
            only_client_types=["mobile"],
            critical=True,
            require_ack=True,
        )

    async def broadcast_question_with_options(self, question_id: str, db):
        """Broadcast question with randomized options using the new system"""
        try:
            # Reset all players' answered status for the new question
            manager.reset_all_players_answered(self.session_code)

            from app.logic.game_logic import broadcast_question_with_options

            await broadcast_question_with_options(self.session_code, question_id, db)
        except Exception as e:
            logger.error(f"Error broadcasting question with options: {e}")
            # Fallback to old system if new one fails
            from app.database.dbCRUD import get_question_by_id

            question = get_question_by_id(question_id, db)
            if question:
                await self.broadcast_question(
                    {
                        "question_id": question.question_id,
                        "question": question.question,
                        "answer": question.answer,
                        "genre": question.genre,
                    }
                )

    def format_question_for_mobile(
        self, question_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format question data for mobile clients - override in subclasses"""
        return question_data


class TriviaGameHandler(GameEventHandler):
    """Handler for trivia game mode"""

    def __init__(self, session_code: str):
        super().__init__(session_code, "trivia")

    async def handle_player_answer(
        self, player_id: str, answer: str, question_id: str, db: Session
    ):
        """Handle trivia answer submission"""
        # Use the same logic as the REST API to ensure game advancement
        from app.logic.game_logic import submit_player_answer

        try:
            # This includes all the logic for checking correctness, updating scores,
            # and automatically advancing the game when all players have answered
            result = submit_player_answer(
                db, self.session_code, player_id, question_id, answer
            )

            # Check if there was an error
            if "error" in result:
                logger.warning(
                    "ANSWER REJECTED session=%s player=%s question=%s error=%s",
                    self.session_code,
                    player_id,
                    question_id,
                    result["error"],
                )

                for connection_info in manager.get_session_connections(
                    self.session_code
                ).values():
                    if (
                        connection_info.get("player_id") == player_id
                        and connection_info.get("client_type") == "mobile"
                    ):
                        await manager.send_personal_message(
                            {
                                "type": "answer_rejected",
                                "data": {
                                    "message": result["error"],
                                    "question_id": question_id,
                                },
                            },
                            connection_info["websocket"],
                        )
                    break

                return

            # Log the result for debugging
            logger.info(f"Answer submission result for player {player_id}: {result}")

            # Mark player as having answered in WebSocket connection info
            manager.set_player_answered(self.session_code, player_id, True)

            # Get player info for broadcasting
            player = get_player_by_ID(db, player_id)
            player_name = player.player_name if player else "Unknown Player"

            # Broadcast to all clients that player answered (with game state update)
            await manager.broadcast_to_session(
                self.session_code,
                {
                    "type": "player_answered",
                    "data": {
                        "player_id": player_id,
                        "roster_player_id": make_roster_player_id(
                            self.session_code, player_id
                        ),
                        "player_name": player_name,
                        "answered_at": datetime.now().isoformat(),
                        "is_correct": result.get("is_correct", False),
                        "game_state": result.get("game_state", {}),
                    },
                },
                critical=True,
            )

            # Also broadcast game status update to ensure frontend stays in sync
            game_status_data = result.get("game_state", {})

            # Add real-time WebSocket-based answered count
            ws_answered_count = manager.get_answered_count(self.session_code)
            game_status_data["playersAnswered"] = ws_answered_count

            logger.info(f"Broadcasting game_status_update: {game_status_data}")
            await manager.broadcast_to_session(
                self.session_code,
                {
                    "type": "game_status_update",
                    "data": game_status_data,
                },
                critical=True,
            )

            # If game advanced to next question, broadcast it
            # Check both possible locations for the action (top level or nested in game_state)
            action = result.get("action") or result.get("game_state", {}).get("action")
            logger.info(f"Detected action after answer submission: {action}")
            logger.info(f"Full answer submission result: {result}")

            if action == "next_question":
                logger.info(f"Revealing next question for session {self.session_code}")
                from app.logic.game_logic import get_game_session_state

                game_state = get_game_session_state(db, self.session_code)
                if game_state and game_state.current_question_id:
                    manager.clear_question_queue(self.session_code)
                    question_start_at = utc_now() + timedelta(
                        milliseconds=NEXT_QUESTION_REVEAL_DELAY_MS
                    )
                    await reveal_current_question(
                        self.session_code,
                        db,
                        iso_utc(question_start_at),
                        acting_player_id=player_id,
                    )
                else:
                    logger.warning("No current question ID found after auto-advance")

            # If game ended, broadcast game end
            elif action == "game_ended":
                logger.info(f"Game ended for session {self.session_code}")
                await handle_game_end(self.session_code, db, acting_player_id=player_id)
            elif action is None:
                logger.info(
                    f" No action needed - waiting for more players or other conditions"
                )

            # Send confirmation to the specific mobile player
            mobile_players = manager.get_mobile_players(self.session_code)
            for connection_id, connection_info in manager.get_session_connections(
                self.session_code
            ).items():
                if (
                    connection_info.get("player_id") == player_id
                    and connection_info.get("client_type") == "mobile"
                ):
                    await manager.send_personal_message(
                        {
                            "type": "answer_submitted",
                            "data": {
                                "message": "Answer submitted successfully!",
                                "can_change_answer": False,
                            },
                        },
                        connection_info["websocket"],
                    )
                    break

        except Exception:
            logger.exception(
                "Error handling trivia answer session=%s player=%s question=%s",
                self.session_code,
                player_id,
                question_id,
            )

    def format_question_for_mobile(
        self, question_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format trivia question for mobile - send FULL question data with all fields"""
        # Determine ui_mode from difficulty if not already set
        difficulty = str(question_data.get("difficulty", "easy")).lower()
        ui_mode = question_data.get("ui_mode")

        if not ui_mode:
            # Calculate ui_mode based on difficulty and options
            has_options = bool(
                question_data.get("display_options") or question_data.get("options")
            )
            if has_options:
                ui_mode = (
                    "multiple_choice"
                    if difficulty in ["easy", "medium"]
                    else "text_input"
                )
            else:
                ui_mode = "text_input"

        return {
            "game_type": "trivia",
            "question_id": question_data.get("question_id"),
            "question": question_data.get("question", ""),
            "genre": question_data.get("genre"),
            "difficulty": question_data.get("difficulty"),
            "display_options": question_data.get(
                "display_options", question_data.get("options", [])
            ),
            "options": question_data.get(
                "display_options", question_data.get("options", [])
            ),  # Alias for compatibility
            "ui_mode": ui_mode,
        }


class BuzzerGameHandler(GameEventHandler):
    """Handler for buzzer game mode"""

    def __init__(self, session_code: str):
        super().__init__(session_code, "buzzer")

    @property
    def buzzer_state(self) -> Dict[str, Any]:
        return manager.get_buzzer_state(self.session_code)

    async def reject_fair_play_locked_buzzer(
        self,
        player_id: str,
        question_id: str,
        db: Session,
    ) -> bool:
        """
        Prevent a Fair Play frozen/kicked player from buzz-locking the round.

        This protects against stale mobile UI where the player returns to the app and
        taps the buzzer before their freeze update has rendered.
        """
        is_locked_by_fair_play = is_player_kicked(
            db, self.session_code, player_id
        ) or is_player_frozen_for_question(
            db,
            self.session_code,
            player_id,
            question_id,
        )
        if not is_locked_by_fair_play:
            return False
        state = self.buzzer_state
        frozen_players = state.setdefault("frozen_players", set())
        frozen_players.add(player_id)

        if state.get("current_buzzer_winner") == player_id:
            state["current_buzzer_winner"] = None

        if not state.get("current_buzzer_winner"):
            state["question_active"] = True
            state["transitioning"] = False
            state["accepting_buzzes"] = True

        logger.info(
            "Rejected Fair Play locked buzzer press: session=%s player=%s question=%s",
            self.session_code,
            safe_player_ref(player_id),
            question_id,
        )
        for connection_info in manager.get_player_connections(
            self.session_code,
            player_id,
        ).values():
            websocket = connection_info.get("websocket")
            if websocket:
                await manager.send_personal_message(
                    {
                        "type": "buzzer_rejected",
                        "data": {
                            "reason": "fair_play_restriction",
                            "question_id": question_id,
                            "message": "You are frozen for this question because of Fair Play Mode.",
                        },
                    },
                    websocket,
                )

        await manager.broadcast_buzzer_state_update(self.session_code)
        await self.update_mobile_buzzer_ui(
            db,
            message_override="Another player was frozen by Fair Play. Buzzing is open again!",
        )
        return True

    async def handle_buzzer_press(
        self, player_id: str, db: Session, incoming_question_id: str = None
    ):
        """Handle player pressing buzzer"""
        state = self.buzzer_state
        phase_state = manager.get_session_phase_state(self.session_code)
        current_question_id = phase_state.get("current_question_id")

        if phase_state.get("phase") != SessionPhase.QUESTION.value:
            return

        if incoming_question_id and incoming_question_id != current_question_id:
            logger.info(
                "Ignoring stale buzzer press session=%s player=%s incoming=%s current=%s",
                self.session_code,
                safe_player_ref(player_id),
                incoming_question_id,
                current_question_id,
            )
            return

        if state.get("current_question_id") != current_question_id:
            return

        if await self.reject_fair_play_locked_buzzer(
            player_id=player_id,
            question_id=current_question_id,
            db=db,
        ):
            return

        if state.get("transitioning") or not state.get("accepting_buzzes", False):
            return

        if not state["question_active"]:
            return

        # Check if player is frozen
        if player_id in state["frozen_players"]:
            return

        # Check if someone already buzzed in
        if state["current_buzzer_winner"]:
            # If the same player taps a stale active buzzer, do not let the UI deadlock.
            # Resend the authoritative buzzer UI so they return to answer mode.
            if state["current_buzzer_winner"] == player_id:
                logger.info(
                    "Current buzzer winner pressed stale buzzer again; resyncing UI: session=%s player=%s question=%s",
                    self.session_code,
                    player_id,
                    current_question_id,
                )
                await manager.broadcast_buzzer_state_update(self.session_code)
                await self.update_mobile_buzzer_ui(db)
            return

        # This player wins the buzzer.
        # Close buzzing immediately so every non-winner is greyed out.
        state["current_buzzer_winner"] = player_id
        state["question_active"] = True
        state["transitioning"] = False
        state["accepting_buzzes"] = False

        logger.warning(
            "BUZZER WINNER LOCKED session=%s player=%s question=%s accepting_buzzes=%s",
            self.session_code,
            safe_player_ref(player_id),
            current_question_id,
            state.get("accepting_buzzes"),
        )

        player = get_player_by_ID(db, player_id)
        player_name = player.player_name if player else "Unknown Player"

        # Notify all clients that a player won the buzzer.
        await manager.broadcast_to_session(
            self.session_code,
            {
                "type": "buzzer_winner",
                "data": {
                    "player_id": player_id,
                    "roster_player_id": make_roster_player_id(
                        self.session_code, player_id
                    ),
                    "player_name": player_name,
                    "question_id": current_question_id,
                    "timestamp": datetime.now().isoformat(),
                },
            },
        )

        # Send generic waiting state first, then personal UI states.
        # The winner gets answer_mode, everyone else gets waiting.
        await manager.broadcast_buzzer_state_update(self.session_code)
        await self.update_mobile_buzzer_ui(db)

    async def handle_player_answer(
        self, player_id: str, answer: str, question_id: str, db: Session
    ):
        """Handle buzzer game answer submission"""
        state = self.buzzer_state
        phase_state = manager.get_session_phase_state(self.session_code)
        current_phase_question_id = phase_state.get("current_question_id")
        state_question_id = state.get("current_question_id")

        if question_id != current_phase_question_id or question_id != state_question_id:
            logger.warning(
                "STALE BUZZER ANSWER REJECTED session=%s player=%s incoming_question=%s phase_question=%s state_question=%s",
                self.session_code,
                player_id,
                question_id,
                current_phase_question_id,
                state_question_id,
            )

            for connection_info in manager.get_player_connections(
                self.session_code,
                player_id,
            ).values():
                websocket = connection_info.get("websocket")
                if websocket:
                    await manager.send_personal_message(
                        {
                            "type": "answer_rejected",
                            "data": {
                                "reason": "stale_question",
                                "message": "That question has already moved on.",
                                "question_id": question_id,
                                "current_question_id": current_phase_question_id,
                            },
                        },
                        websocket,
                    )

            await manager.broadcast_buzzer_state_update(self.session_code)
            await self.update_mobile_buzzer_ui(db)
            return
        # Only the current buzzer winner can answer
        if state["current_buzzer_winner"] != player_id:
            return

        from app.logic.game_logic import submit_player_answer

        submission_result = submit_player_answer(
            db, self.session_code, player_id, question_id, answer
        )
        if "error" in submission_result:
            logger.warning(
                f"Could not record buzzer answer for {player_id}: {submission_result['error']}"
            )
            return
        action = submission_result.get("game_state", {}).get("action")
        is_correct = submission_result.get("is_correct", False)

        player = get_player_by_ID(db, player_id)
        player_name = player.player_name if player else "Unknown Player"

        if is_correct:
            # Correct answer - move to next question
            await manager.broadcast_to_session(
                self.session_code,
                {
                    "type": "correct_answer",
                    "data": {
                        "player_id": player_id,
                        "roster_player_id": make_roster_player_id(
                            self.session_code, player_id
                        ),
                        "player_name": player_name,
                        "answer": answer,
                        "correct": True,
                    },
                },
            )

            await self.lock_buzzer_until_next_question("Moving to the next question...")
            if action == "next_question":
                await self.reveal_current_db_question(
                    db,
                    "buzzer_correct_answer",
                    acting_player_id=player_id,
                )
            elif action == "game_ended":
                await handle_game_end(self.session_code, db, acting_player_id=player_id)
            else:
                await advance_or_end_current_question(
                    self.session_code,
                    db,
                    reason="buzzer_correct_answer",
                    acting_player_id=player_id,
                )

        else:
            if action == "next_question":
                await self.lock_buzzer_until_next_question(
                    "Waiting for the next question..."
                )
                await self.reveal_current_db_question(
                    db,
                    "buzzer_all_answered",
                    acting_player_id=player_id,
                )
                return
            if action == "game_ended":
                await self.lock_buzzer_until_next_question(
                    "Waiting for final scores..."
                )
                await handle_game_end(self.session_code, db, acting_player_id=player_id)
                return

            # Wrong answer - freeze this player and reset buzzer
            state["frozen_players"].add(player_id)
            state["current_buzzer_winner"] = None
            state["attempts"].append(
                {
                    "player_id": player_id,
                    "answer": answer,
                    "correct": False,
                    "timestamp": datetime.now().isoformat(),
                }
            )

            await manager.broadcast_to_session(
                self.session_code,
                {
                    "type": "incorrect_answer",
                    "data": {
                        "player_id": player_id,
                        "roster_player_id": make_roster_player_id(
                            self.session_code, player_id
                        ),
                        "player_name": player_name,
                        "answer": answer,
                        "correct": False,
                        "frozen_players": list(state["frozen_players"]),
                        "frozen_roster_player_ids": [
                            make_roster_player_id(self.session_code, frozen_id)
                            for frozen_id in state["frozen_players"]
                        ],
                    },
                },
            )

            # Check if all players are frozen before reopening the buzzer.
        active_players = len(manager.get_mobile_players(self.session_code))
        if active_players and len(state["frozen_players"]) >= active_players:
            await manager.broadcast_to_session(
                self.session_code,
                {
                    "type": "question_failed",
                    "data": {
                        "question_id": question_id,
                        "reason": "all_players_frozen",
                    },
                },
                critical=True,
            )
            await self.lock_buzzer_until_next_question(
                "Waiting for the next question..."
            )
            await advance_or_end_current_question(
                self.session_code,
                db,
                reason="buzzer_all_wrong",
                acting_player_id=player_id,
            )
            return

        # Wrong answer, but other players can still buzz.
        # Reopen buzzing only for non-frozen players.
        state["question_active"] = True
        state["transitioning"] = False
        state["accepting_buzzes"] = True

        logger.warning(
            "BUZZER REOPENED AFTER WRONG ANSWER session=%s question=%s frozen_count=%s active_players=%s",
            self.session_code,
            question_id,
            len(state["frozen_players"]),
            active_players,
        )

        await manager.broadcast_buzzer_state_update(self.session_code)
        await self.update_mobile_buzzer_ui(db)

    async def start_question(self, question_data: Dict[str, Any]):
        """Start a new buzzer question"""
        manager.start_buzzer_question(
            self.session_code, question_data.get("question_id")
        )

        await self.broadcast_question(question_data)
        await manager.broadcast_buzzer_state_update(self.session_code)
        await self.update_mobile_buzzer_ui()

    async def update_mobile_buzzer_ui(
        self, db: Session = None, message_override: str = None
    ):
        """Update mobile UI based on the authoritative buzzer state."""
        mobile_connections = manager.get_session_connections(self.session_code)
        state = self.buzzer_state
        phase_state = manager.get_session_phase_state(self.session_code)

        expected_question_id = state.get("current_question_id") or phase_state.get(
            "current_question_id"
        )

        def question_model_to_dict(question_model):
            if not question_model:
                return None

            try:
                from app.logic.game_logic import build_question_with_randomized_options

                question_details = build_question_with_randomized_options(
                    question_model
                )

                difficulty = question_details.get("difficulty")
                if hasattr(difficulty, "value"):
                    difficulty = difficulty.value

                return {
                    "question_id": question_details.get("question_id"),
                    "question": question_details.get("question"),
                    "answer": question_details.get("answer"),
                    "genre": question_details.get("genre"),
                    "difficulty": difficulty or "easy",
                    "display_options": question_details.get("display_options", []),
                    "options": question_details.get("display_options", []),
                    "correct_index": question_details.get("correct_index"),
                    "game_type": "buzzer",
                }

            except Exception:
                logger.exception(
                    "Could not build randomized buzzer fallback options for question=%s",
                    getattr(question_model, "question_id", None),
                )

                difficulty = getattr(question_model, "difficulty", None)
                if hasattr(difficulty, "value"):
                    difficulty = difficulty.value

                raw_options = getattr(question_model, "question_options", None) or []
                correct_answer = getattr(question_model, "answer", None)

                options = list(raw_options) if isinstance(raw_options, list) else []

                if correct_answer and correct_answer not in options:
                    options.append(correct_answer)

                return {
                    "question_id": getattr(question_model, "question_id", None),
                    "question": getattr(question_model, "question", None),
                    "answer": correct_answer,
                    "genre": getattr(question_model, "genre", None),
                    "difficulty": difficulty or "easy",
                    "display_options": options,
                    "options": options,
                    "game_type": "buzzer",
                }

        current_question = None

        if db and state.get("current_buzzer_winner"):
            question_status = get_current_question_details(db, self.session_code)
            candidate_question = (
                question_status.get("current_question") if question_status else None
            )
            candidate_question_id = (
                candidate_question.get("question_id")
                if isinstance(candidate_question, dict)
                else None
            )

            if (
                expected_question_id
                and candidate_question_id
                and candidate_question_id != expected_question_id
            ):
                logger.warning(
                    "BUZZER UI QUESTION MISMATCH session=%s expected=%s candidate=%s; fetching expected question directly",
                    self.session_code,
                    expected_question_id,
                    candidate_question_id,
                )
                question_model = get_question_by_id(expected_question_id, db)
                current_question = question_model_to_dict(question_model)
            elif candidate_question:
                current_question = candidate_question
            elif expected_question_id:
                logger.warning(
                    "BUZZER UI QUESTION MISSING session=%s expected=%s; fetching expected question directly",
                    self.session_code,
                    expected_question_id,
                )
                question_model = get_question_by_id(expected_question_id, db)
                current_question = question_model_to_dict(question_model)

            logger.warning(
                "BUZZER UI ANSWER PAYLOAD SOURCE session=%s winner=%s expected_question=%s payload_question=%s question_text=%s",
                self.session_code,
                state.get("current_buzzer_winner"),
                expected_question_id,
                current_question.get("question_id") if current_question else None,
                current_question.get("question") if current_question else None,
            )

        winner_name = None
        if db and state.get("current_buzzer_winner"):
            winner = get_player_by_ID(db, state["current_buzzer_winner"])
            winner_name = winner.player_name if winner else "the current player"

        for connection_id, connection_info in mobile_connections.items():
            if connection_info.get("client_type") != "mobile":
                continue

            player_id = connection_info.get("player_id")

            ui_state = {
                "game_type": "buzzer",
                "ui_mode": "buzzer",
                "question_id": expected_question_id,
                "transitioning": state.get("transitioning", False),
                "accepting_buzzes": state.get("accepting_buzzes", False),
                "is_current_player": False,
            }

            if state.get("transitioning"):
                ui_state["button_state"] = "waiting"
                ui_state["is_current_player"] = False
                ui_state["transitioning"] = True
                ui_state["accepting_buzzes"] = False
                ui_state["message"] = (
                    message_override or "Waiting for the next question..."
                )

            elif player_id in state["frozen_players"]:
                ui_state["button_state"] = "frozen"
                ui_state["is_current_player"] = False
                ui_state["transitioning"] = False
                ui_state["accepting_buzzes"] = False
                ui_state["message"] = "You're frozen out this round!"

            elif state["current_buzzer_winner"] == player_id:
                if not current_question:
                    logger.warning(
                        "BUZZER WINNER UI CANNOT LOAD QUESTION session=%s player=%s expected_question=%s",
                        self.session_code,
                        safe_player_ref(player_id),
                        expected_question_id,
                    )
                    ui_state["button_state"] = "waiting"
                    ui_state["is_current_player"] = False
                    ui_state["transitioning"] = False
                    ui_state["accepting_buzzes"] = False
                    ui_state["message"] = "Syncing question..."
                else:
                    answer_payload = self.format_buzzer_answer_payload(current_question)
                    ui_state.update(answer_payload)
                    ui_state["question_id"] = expected_question_id
                    ui_state["button_state"] = "answer_mode"
                    ui_state["is_current_player"] = True
                    ui_state["transitioning"] = False
                    ui_state["accepting_buzzes"] = False
                    ui_state["message"] = answer_payload.get(
                        "message", "You buzzed first. Choose your answer."
                    )

            elif state["current_buzzer_winner"]:
                ui_state["button_state"] = "waiting"
                ui_state["is_current_player"] = False
                ui_state["transitioning"] = False
                ui_state["accepting_buzzes"] = False
                ui_state["current_buzzer_winner"] = state["current_buzzer_winner"]
                ui_state["message"] = (
                    f"Waiting for {winner_name or 'the current player'} to answer..."
                )

            elif not state["question_active"] or not state.get(
                "accepting_buzzes", False
            ):
                ui_state["button_state"] = "waiting"
                ui_state["is_current_player"] = False
                ui_state["transitioning"] = False
                ui_state["accepting_buzzes"] = False
                ui_state["message"] = (
                    message_override or "Waiting for the next question..."
                )

            else:
                ui_state["button_state"] = "active"
                ui_state["is_current_player"] = False
                ui_state["transitioning"] = False
                ui_state["accepting_buzzes"] = True
                ui_state["message"] = "Press to buzz in!"

            logger.warning(
                "BUZZER UI SEND session=%s player=%s button=%s is_current=%s question_id=%s message=%s",
                self.session_code,
                safe_player_ref(player_id),
                ui_state.get("button_state"),
                ui_state.get("is_current_player"),
                ui_state.get("question_id"),
                ui_state.get("message"),
            )

            await manager.send_personal_message(
                {"type": "ui_update", "data": ui_state}, connection_info["websocket"]
            )

    def format_buzzer_answer_payload(
        self, question_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build answer-mode data for the buzzer winner only."""
        question_data = question_data or {}
        difficulty = str(question_data.get("difficulty") or "easy").lower()
        options = (
            question_data.get("display_options") or question_data.get("options") or []
        )
        uses_text_input = difficulty == "hard" or not options
        display_options = [] if uses_text_input else options
        ui_mode = "text_input" if uses_text_input else "multiple_choice"

        return {
            "ui_mode": ui_mode,
            "question_id": question_data.get("question_id"),
            "question": question_data.get("question"),
            "genre": question_data.get("genre"),
            "difficulty": question_data.get("difficulty"),
            "display_options": display_options,
            "options": display_options,
            "message": (
                "You buzzed first. Enter your answer."
                if uses_text_input
                else "You buzzed first. Choose your answer."
            ),
        }

    async def reveal_current_db_question(
        self,
        db: Session,
        reason: str,
        acting_player_id: Optional[str] = None,
    ) -> bool:
        """Reveal the DB's current question after answer logic has already advanced it."""
        from app.logic.game_logic import get_game_session_state

        game_state = get_game_session_state(db, self.session_code)
        if not game_state or not game_state.current_question_id:
            logger.warning(
                f"No current question to reveal for {self.session_code} after {reason}"
            )
            return False

        manager.clear_question_queue(self.session_code)
        question_start_at = utc_now() + timedelta(
            milliseconds=NEXT_QUESTION_REVEAL_DELAY_MS
        )
        return await reveal_current_question(
            self.session_code,
            db,
            iso_utc(question_start_at),
            acting_player_id=acting_player_id,
        )

    async def reset_buzzer_state(self):
        """Reset buzzer state for next question"""
        manager.reset_buzzer_state(self.session_code)
        await manager.broadcast_buzzer_state_update(self.session_code)

    async def lock_buzzer_until_next_question(
        self, message: str = "Waiting for the next question..."
    ):
        """Lock buzzers without reopening the old question."""
        manager.lock_buzzer_until_next_question(self.session_code)
        await manager.broadcast_buzzer_state_update(self.session_code)
        await self.update_mobile_buzzer_ui(message_override=message)

    async def check_answer_correctness(
        self, answer: str, question_id: str, db: Session
    ) -> bool:
        """Check if the answer is correct using the shared validation service."""
        try:
            from app.logic.game_logic import question_allows_fuzzy_validation

            question = get_question_by_id(question_id, db)
            if not question:
                return False
            return validate_answer_against_question(
                answer,
                question,
                allow_fuzzy=question_allows_fuzzy_validation(question),
            ).is_correct
        except Exception as e:
            logger.error(f"Error checking answer: {e}")
            return False

    def format_question_for_mobile(
        self, question_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format buzzer question for mobile - just show buzzer button"""
        return {
            "game_type": "buzzer",
            "question_id": question_data.get("question_id"),
            "ui_mode": "buzzer",
            "button_state": "active",
            "message": "Get ready to buzz in!",
        }


# Game handler factory
GAME_HANDLERS = {
    "trivia": TriviaGameHandler,
    "buzzer": BuzzerGameHandler,
}


def create_game_handler(session_code: str, game_type: str) -> GameEventHandler:
    """Create appropriate game handler based on game type"""
    handler_class = GAME_HANDLERS.get(game_type.lower(), TriviaGameHandler)
    return handler_class(session_code)
