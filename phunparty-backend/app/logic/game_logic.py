"""
Game Logic Module - Handles automatic game progression (Business Logic Only)
All database operations are delegated to dbCRUD.py
"""

import json
import random
import logging
from sqlalchemy.orm import Session

from app.database.dbCRUD import (
    advance_to_next_question,
    count_responses_for_question,
    create_player_response,
    get_game_session_state,
    get_number_of_players_in_session,
    get_player_response,
    get_question_by_id,
    update_game_state_waiting_status,
    update_scores,
)

logger = logging.getLogger(__name__)


def submit_player_answer(
    db: Session, session_code: str, player_id: str, question_id: str, player_answer: str
) -> dict:
    """
    Submit a player's answer and check if all players have answered
    Returns game state information
    """
    # Check if player already answered this question
    existing_response = get_player_response(db, session_code, player_id, question_id)
    if existing_response:
        return {"error": "Player has already answered this question"}

    # Get the correct answer and validate
    question = get_question_by_id(question_id, db)
    if not question:
        raise ValueError("Question not found")

    # For simple answer model, compare directly with the answer field
    is_correct = player_answer.lower().strip() == str(question.answer).lower().strip()

    # Record the player's response
    create_player_response(
        db, session_code, player_id, question_id, player_answer, is_correct
    )

    # Update score if correct
    if is_correct:
        try:
            update_scores(db, session_code, player_id)
        except Exception:
            # If score doesn't exist, this will be handled by the scores system
            pass

    # Check if all players have answered this question
    game_progression = check_and_advance_game(db, session_code, question_id)

    return {
        "player_answer": player_answer,
        "is_correct": is_correct,
        "game_state": game_progression,
    }


def updateGameStartStatus(db: Session, session_code: str, is_started: bool) -> None:
    """
    Update the game's started status
    """
    game_state = get_game_session_state(db, session_code)
    if not game_state:
        raise ValueError("Game session not found")

    game_state.isstarted = is_started
    db.commit()


def check_and_advance_game(
    db: Session, session_code: str, current_question_id: str
) -> dict:
    """
    Check if all players have answered and advance the game if needed
    """
    try:
        # Get counts from database
        players_in_session = get_number_of_players_in_session(db, session_code)
        responses_to_question = count_responses_for_question(
            db, session_code, current_question_id
        )

        # Get current game state
        game_state = get_game_session_state(db, session_code)
        if not game_state:
            raise ValueError("Game state not found")

        result = {
            "players_total": players_in_session,
            "players_answered": responses_to_question,
            "waiting_for_players": responses_to_question < players_in_session,
            "current_question_index": game_state.current_question_index,
            "total_questions": game_state.total_questions,
            "game_state": "active",
        }

        # If all players have answered
        if responses_to_question >= players_in_session:
            logger.info(
                f"All players ({responses_to_question}/{players_in_session}) have answered question {current_question_id}"
            )

            # Update waiting status
            update_game_state_waiting_status(db, session_code, False)

            # Check if there are more questions
            if game_state.current_question_index + 1 < game_state.total_questions:
                logger.info(
                    f"Advancing to next question. Current index: {game_state.current_question_index}, Total: {game_state.total_questions}"
                )
                # Advance to next question
                advancement_result = advance_to_next_question(db, session_code)
                logger.info(f"Advancement result: {advancement_result}")
                result.update(advancement_result)
            else:
                logger.info(
                    f"Game ending. No more questions after index {game_state.current_question_index}"
                )
                # No more questions, end the game
                from app.database.dbCRUD import end_game_session

                advancement_result = end_game_session(db, session_code)
                result.update(advancement_result)
        else:
            logger.info(
                f"Waiting for more players to answer. {responses_to_question}/{players_in_session} have answered"
            )

        logger.info(f"Final check_and_advance_game result: {result}")
        return result
    except Exception as e:
        return {"error": str(e)}


def get_current_question_for_session(db: Session, session_code: str) -> dict:
    """
    Get the current question for a game session
    """
    # Get current game state
    game_state = get_game_session_state(db, session_code)
    if not game_state:
        raise ValueError("Game session not found")

    if not game_state.is_active:
        return {"message": "Game has ended", "is_active": False}

    # Get the current question
    current_question = get_question_by_id(game_state.current_question_id, db)
    if not current_question:
        raise ValueError("Current question not found")

    return {
        "question_id": current_question.question_id,
        "question": current_question.question,
        "genre": current_question.genre,
        "question_index": game_state.current_question_index,
        "total_questions": game_state.total_questions,
        "is_waiting_for_players": game_state.is_waiting_for_players,
    }


def get_question_with_randomized_options(db: Session, question_id: str) -> dict:
    """
    Get a question with randomized multiple choice options
    Returns the same format as the questions route for consistency
    """
    try:
        question = get_question_by_id(question_id, db)
        if not question:
            raise ValueError("Question not found")

        # Handle questions that might not have options yet
        if not question.question_options:
            logger.warning(f"Question {question_id} has no question_options")
            return {
                "question_id": question.question_id,
                "question": question.question,
                "answer": question.answer,
                "genre": question.genre,
                "difficulty": (
                    question.difficulty.value if question.difficulty else "easy"
                ),
                "question_options": [],
                "display_options": [question.answer],  # Just the correct answer
                "correct_index": 0,
            }

        # Parse and randomize the options
        try:
            incorrect_options = json.loads(question.question_options)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"Invalid JSON in question_options for question {question_id}")
            incorrect_options = []

        # Combine incorrect options with correct answer
        all_options = incorrect_options + [question.answer]
        random.shuffle(all_options)
        correct_index = all_options.index(question.answer)

        return {
            "question_id": question.question_id,
            "question": question.question,
            "answer": question.answer,
            "genre": question.genre,
            "difficulty": question.difficulty.value if question.difficulty else "easy",
            "question_options": question.question_options,
            "display_options": all_options,
            "correct_index": correct_index,
        }

    except Exception as e:
        logger.error(f"Error getting question with options: {e}")
        raise


async def broadcast_question_with_options(
    session_code: str, question_id: str, db: Session
):
    """
    Broadcast a question with randomized display options to all players in a session
    """
    try:
        from app.websockets.manager import manager

        # Get question with randomized options
        question_data = get_question_with_randomized_options(db, question_id)

        # Create message for mobile players (without correct answer info)
        player_message = {
            "type": "new_question",
            "data": {
                "question_id": question_data["question_id"],
                "question": question_data["question"],
                "genre": question_data["genre"],
                "difficulty": question_data["difficulty"],
                "display_options": question_data["display_options"],
                "question_index": None,  # Will be added by caller if needed
                "total_questions": None,  # Will be added by caller if needed
            },
        }

        # Create message for web host (with correct answer info)
        host_message = {
            "type": "new_question",
            "data": {
                **player_message["data"],
                "answer": question_data["answer"],
                "correct_index": question_data["correct_index"],
                "question_options": question_data["question_options"],
            },
        }

        # Send to mobile players (without answer)
        await manager.broadcast_to_mobile_players(session_code, player_message)

        # Send to web host (with answer info)
        await manager.broadcast_to_web_clients(session_code, host_message)

        logger.info(
            f"Broadcasted question {question_id} with options to session {session_code}"
        )

    except Exception as e:
        logger.error(f"Failed to broadcast question with options: {e}")
        # Send error message to all clients
        await manager.broadcast_to_session(
            session_code,
            {"type": "error", "data": {"message": "Failed to load question"}},
        )
