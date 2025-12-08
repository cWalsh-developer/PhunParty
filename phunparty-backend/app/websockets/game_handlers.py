"""
Game event handlers for different game types
Handles the business logic for different game modes
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database.dbCRUD import (
    create_player_response,
    get_current_question_details,
    get_game_session_state,
    get_player_by_ID,
)
from app.websockets.manager import manager

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

        logger.info(f"Broadcasting question to session {self.session_code}")

        # Send full question to web clients with critical flag
        await manager.broadcast_to_session(
            self.session_code,
            {
                "type": "question_started",
                "data": {"question": question_data, "game_type": self.game_type},
            },
            only_client_types=["web"],
            critical=True,
        )

        # Send appropriate mobile UI data to mobile clients with critical flag
        mobile_data = self.format_question_for_mobile(question_data)
        await manager.broadcast_to_session(
            self.session_code,
            {"type": "question_started", "data": mobile_data},
            only_client_types=["mobile"],
            critical=True,
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
                logger.error(f"Error submitting answer: {result['error']}")
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
                logger.info(
                    f"Advancing to next question for session {self.session_code}"
                )
                # Get the new question details and broadcast with options
                from app.logic.game_logic import get_game_session_state

                game_state = get_game_session_state(db, self.session_code)
                if game_state and game_state.current_question_id:
                    logger.info(
                        f"Broadcasting new question {game_state.current_question_id} with options"
                    )
                    await self.broadcast_question_with_options(
                        game_state.current_question_id, db
                    )
                else:
                    logger.warning(
                        "No current question ID found, using fallback method"
                    )
                    # Fallback to old method if no current question ID
                    from app.database.dbCRUD import get_current_question_details

                    current_question = get_current_question_details(
                        db, self.session_code
                    )
                    if current_question:
                        logger.info(f"📝 Broadcasting question via fallback method")
                        await self.broadcast_question(current_question)
                    else:
                        logger.error("No current question found via fallback method")

            # If game ended, broadcast game end
            elif action == "game_ended":
                logger.info(f"🏁 Game ended for session {self.session_code}")
                await manager.broadcast_to_session(
                    self.session_code,
                    {
                        "type": "game_ended",
                        "data": result.get("game_state", {}),
                    },
                )
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

        except Exception as e:
            logger.error(f"Error handling trivia answer: {e}")

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
            "correct_index": question_data.get("correct_index"),
            "answer": question_data.get("answer"),
        }


class BuzzerGameHandler(GameEventHandler):
    """Handler for buzzer game mode"""

    def __init__(self, session_code: str):
        super().__init__(session_code, "buzzer")
        self.buzzer_state = {
            "current_buzzer_winner": None,
            "frozen_players": set(),
            "question_active": False,
            "attempts": [],
        }

    async def handle_buzzer_press(self, player_id: str, db: Session):
        """Handle player pressing buzzer"""
        if not self.buzzer_state["question_active"]:
            return

        # Check if player is frozen
        if player_id in self.buzzer_state["frozen_players"]:
            return

        # Check if someone already buzzed in
        if self.buzzer_state["current_buzzer_winner"]:
            return

        # This player wins the buzzer!
        self.buzzer_state["current_buzzer_winner"] = player_id

        player = get_player_by_ID(db, player_id)
        player_name = player.player_name if player else "Unknown Player"

        # Notify all clients
        await manager.broadcast_to_session(
            self.session_code,
            {
                "type": "buzzer_winner",
                "data": {
                    "player_id": player_id,
                    "player_name": player_name,
                    "timestamp": datetime.now().isoformat(),
                },
            },
        )

        # Update mobile UI - winner can now answer, others wait
        await self.update_mobile_buzzer_ui()

    async def handle_player_answer(
        self, player_id: str, answer: str, question_id: str, db: Session
    ):
        """Handle buzzer game answer submission"""
        # Only the current buzzer winner can answer
        if self.buzzer_state["current_buzzer_winner"] != player_id:
            return

        # Check if answer is correct (you'll need to implement this logic)
        is_correct = await self.check_answer_correctness(answer, question_id, db)

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
                        "player_name": player_name,
                        "answer": answer,
                        "correct": True,
                    },
                },
            )

            # Reset buzzer state for next question
            await self.reset_buzzer_state()

        else:
            # Wrong answer - freeze this player and reset buzzer
            self.buzzer_state["frozen_players"].add(player_id)
            self.buzzer_state["current_buzzer_winner"] = None
            self.buzzer_state["attempts"].append(
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
                        "player_name": player_name,
                        "answer": answer,
                        "correct": False,
                        "frozen_players": list(self.buzzer_state["frozen_players"]),
                    },
                },
            )

            # Check if all players are frozen
            active_players = len(manager.get_mobile_players(self.session_code))
            if len(self.buzzer_state["frozen_players"]) >= active_players:
                # Unfreeze all players for another round
                self.buzzer_state["frozen_players"].clear()

            await self.update_mobile_buzzer_ui()

    async def start_question(self, question_data: Dict[str, Any]):
        """Start a new buzzer question"""
        self.buzzer_state["question_active"] = True
        self.buzzer_state["current_buzzer_winner"] = None

        await self.broadcast_question(question_data)
        await self.update_mobile_buzzer_ui()

    async def update_mobile_buzzer_ui(self):
        """Update mobile UI based on current buzzer state"""
        mobile_connections = manager.get_session_connections(self.session_code)

        for connection_id, connection_info in mobile_connections.items():
            if connection_info.get("client_type") != "mobile":
                continue

            player_id = connection_info.get("player_id")

            ui_state = {"game_type": "buzzer", "ui_mode": "buzzer"}

            if player_id in self.buzzer_state["frozen_players"]:
                ui_state["button_state"] = "frozen"
                ui_state["message"] = "You're frozen out this round!"
            elif self.buzzer_state["current_buzzer_winner"] == player_id:
                ui_state["button_state"] = "answer_mode"
                ui_state["message"] = "You buzzed in! Enter your answer:"
                ui_state["ui_mode"] = "text_input"
            elif self.buzzer_state["current_buzzer_winner"]:
                ui_state["button_state"] = "waiting"
                ui_state["message"] = (
                    f"Waiting for {self.buzzer_state['current_buzzer_winner']} to answer..."
                )
            else:
                ui_state["button_state"] = "active"
                ui_state["message"] = "Press to buzz in!"

            await manager.send_personal_message(
                {"type": "ui_update", "data": ui_state}, connection_info["websocket"]
            )

    async def reset_buzzer_state(self):
        """Reset buzzer state for next question"""
        self.buzzer_state = {
            "current_buzzer_winner": None,
            "frozen_players": set(),
            "question_active": False,
            "attempts": [],
        }

    async def check_answer_correctness(
        self, answer: str, question_id: str, db: Session
    ) -> bool:
        """Check if the answer is correct - implement your logic here"""
        # This is a placeholder - implement your answer checking logic
        try:
            question_details = get_current_question_details(db, self.session_code)
            correct_answer = question_details.get("answer", "").lower().strip()
            return answer.lower().strip() == correct_answer
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
