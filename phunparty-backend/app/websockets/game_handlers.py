"""
Game event handlers for different game types
Handles the business logic for different game modes
"""

import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

from app.websockets.manager import manager
from app.database.dbCRUD import (
    get_game_session_state,
    get_current_question_details,
    create_player_response,
    get_player_by_ID,
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
        # Send full question to web clients
        await manager.broadcast_to_web_clients(
            self.session_code,
            {
                "type": "question_started",
                "data": {"question": question_data, "game_type": self.game_type},
            },
        )

        # Send appropriate mobile UI data to mobile clients
        mobile_data = self.format_question_for_mobile(question_data)
        await manager.broadcast_to_mobile_players(
            self.session_code, {"type": "question_started", "data": mobile_data}
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
        # Store answer in database
        try:
            create_player_response(
                db, self.session_code, player_id, question_id, answer
            )

            # Get player info
            player = get_player_by_ID(db, player_id)
            player_name = player.player_name if player else "Unknown Player"

            # Broadcast to web clients that player answered
            await manager.broadcast_to_web_clients(
                self.session_code,
                {
                    "type": "player_answered",
                    "data": {
                        "player_id": player_id,
                        "player_name": player_name,
                        "answered_at": datetime.now().isoformat(),
                    },
                },
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
        """Format trivia question for mobile - show question and options"""
        return {
            "game_type": "trivia",
            "question": question_data.get("question", ""),
            "options": question_data.get("options", []),
            "question_id": question_data.get("question_id"),
            "ui_mode": "multiple_choice",
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
