"""
Game Logic Module - Handles automatic game progression (Business Logic Only)
All database operations are delegated to dbCRUD.py
"""

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
        "game_status": "active",
    }

    # If all players have answered
    if responses_to_question >= players_in_session:
        # Update waiting status
        update_game_state_waiting_status(db, session_code, False)

        # Check if there are more questions
        if game_state.current_question_index + 1 < game_state.total_questions:
            # Advance to next question
            advancement_result = advance_to_next_question(db, session_code)
            result.update(advancement_result)
        else:
            # No more questions, end the game
            from app.database.dbCRUD import end_game_session

            advancement_result = end_game_session(db, session_code)
            result.update(advancement_result)

    return result


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
