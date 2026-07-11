"""
Game Logic Module - Handles automatic game progression (Business Logic Only)
All database operations are delegated to dbCRUD.py
"""

import json
import logging
import random
from datetime import UTC, datetime

from requests import session

from app.database.dbCRUD import (
    advance_to_next_question,
    count_responses_for_question,
    create_player_response,
    get_game_session_state,
    lock_game_session_state_for_update,
    get_number_of_players_in_session,
    get_player_response,
    get_question_by_id,
    get_session_by_code,
    update_game_state_waiting_status,
    update_scores,
)
from app.database.fair_play_crud import (
    count_fair_play_resolved_players_for_question,
    count_kicked_players,
    is_player_frozen_for_question,
    is_player_kicked,
)
from app.logic.answer_validation import validate_answer_against_question
from app.security.question_payload import sanitize_question_for_client
from app.security.rls import set_rls_current_player
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def question_allows_fuzzy_validation(question) -> bool:
    """Fuzzy validation is only for free-text answers, not multiple choice."""
    difficulty = getattr(question, "difficulty", None)
    difficulty_value = getattr(difficulty, "value", difficulty)
    if isinstance(difficulty_value, str) and difficulty_value.lower() == "hard":
        return True

    question_options = getattr(question, "question_options", None)
    return not bool(question_options)


def submit_player_answer(
    db: Session, session_code: str, player_id: str, question_id: str, player_answer: str
) -> dict:
    """
    Submit a player's answer and check if all players have answered
    Returns game state information
    """
    game_state = get_game_session_state(db, session_code)
    if not game_state:
        raise ValueError("Game state not found")
    if not game_state.is_active or not game_state.isstarted:
        return {"error": "Game is not accepting answers"}

    authoritative_question_id = game_state.current_question_id
    if not authoritative_question_id:
        return {"error": "No active question"}
    if question_id and question_id != authoritative_question_id:
        logger.warning(
            "Rejected answer for non-current question: session=%s player=%s incoming=%s current=%s",
            session_code,
            player_id,
            question_id,
            authoritative_question_id,
        )
        return {"error": "Question is no longer active"}

    fair_play_enabled = getattr(game_state, "fair_play_enabled", False) is True

    if fair_play_enabled:
        if is_player_kicked(db, session_code, player_id) is True:
            return {"error": "Player has been removed from this session"}

        if (
            is_player_frozen_for_question(
                db, session_code, player_id, authoritative_question_id
            )
            is True
        ):
            return {"error": "Player is frozen for this question"}

    existing_response = get_player_response(
        db, session_code, player_id, authoritative_question_id
    )
    if existing_response:
        return {"error": "Player has already answered this question"}

    # Get the correct answer and validate
    question = get_question_by_id(authoritative_question_id, db)
    if not question:
        raise ValueError("Question not found")

    validation = validate_answer_against_question(
        player_answer,
        question,
        allow_fuzzy=question_allows_fuzzy_validation(question),
    )
    is_correct = validation.is_correct

    # Record the player's response
    try:
        create_player_response(
            db,
            session_code,
            player_id,
            authoritative_question_id,
            player_answer,
            is_correct,
        )
    except IntegrityError:
        db.rollback()
        return {"error": "Player has already answered this question"}

    # Update score if correct
    if is_correct:
        update_scores(db, session_code, player_id)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception(
            "ANSWER SUBMIT COMMIT FAILED session=%s player=%s question=%s",
            session_code,
            player_id,
            authoritative_question_id,
        )
        return {
            "error": str(exc),
            "question_id": authoritative_question_id,
        }

    # Game progression mutates session-owned state. The answer is committed before
    # the cheap readiness check so concurrent final answers can observe each other.
    session = get_session_by_code(db, session_code)
    progression_actor_id = (
        session.owner_player_id if session and session.owner_player_id else player_id
    )

    logger.warning(
        "ANSWER PROGRESSION CONTEXT session=%s answering_player=%s progression_actor=%s",
        session_code,
        player_id,
        progression_actor_id,
    )

    set_rls_current_player(db, progression_actor_id)

    game_state_for_progression = get_game_session_state(db, session_code)
    if not game_state_for_progression:
        game_progression = {"error": "Game state not found"}
    elif game_state_for_progression.current_question_id != authoritative_question_id:
        game_progression = {
            "waiting_for_players": False,
            "current_question_index": game_state_for_progression.current_question_index,
            "total_questions": game_state_for_progression.total_questions,
            "game_state": (
                "active" if game_state_for_progression.isstarted else "waiting"
            ),
            "currentQuestion": game_state_for_progression.current_question_index + 1,
            "totalQuestions": game_state_for_progression.total_questions,
            "isstarted": game_state_for_progression.isstarted,
            "is_active": game_state_for_progression.is_active,
            "stale_question": True,
        }
    else:
        game_progression = check_progression_readiness_without_lock(
            db,
            session_code,
            authoritative_question_id,
            game_state_for_progression,
        )

        if game_progression.get("ready_for_progression"):
            game_progression = check_and_advance_game(
                db, session_code, authoritative_question_id
            )

    if "error" in game_progression:
        db.rollback()
        logger.warning(
            "ANSWER RECORDED BUT PROGRESSION FAILED session=%s player=%s question=%s reason=%s",
            session_code,
            player_id,
            authoritative_question_id,
            game_progression["error"],
        )
    elif game_progression.get("ready_for_progression") or game_progression.get(
        "action"
    ):
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception(
                "ANSWER PROGRESSION COMMIT FAILED session=%s player=%s question=%s",
                session_code,
                player_id,
                authoritative_question_id,
            )
            game_progression = {"error": str(exc)}

    # Restore the original answering-player context for anything else this request does.
    set_rls_current_player(db, player_id)
    return {
        "player_answer": player_answer,
        "question_id": authoritative_question_id,
        "is_correct": is_correct,
        "answer_match": {
            "method": validation.method,
            "score": validation.score,
        },
        "game_state": game_progression,
    }


def check_progression_readiness_without_lock(
    db: Session, session_code: str, current_question_id: str, game_state
) -> dict:
    """
    Build the normal answer-progress snapshot without taking the progression row lock.

    This is an early-exit optimization for the common case where a question is still
    waiting for more players. The locked progression path still performs the
    authoritative re-check before mutating session state.
    """
    total_players = get_number_of_players_in_session(db, session_code)
    kicked_players = count_kicked_players(db, session_code)
    players_in_session = max(0, total_players - kicked_players)
    responses_to_question = count_responses_for_question(
        db, session_code, current_question_id
    )
    fair_play_resolved_players = count_fair_play_resolved_players_for_question(
        db, session_code, current_question_id
    )
    resolved_players = min(
        players_in_session, responses_to_question + fair_play_resolved_players
    )
    ready_for_progression = total_players > 0 and resolved_players >= players_in_session
    frontend_game_state = "active" if game_state.isstarted else "waiting"
    current_question_index = getattr(game_state, "current_question_index", 0)
    total_questions = getattr(game_state, "total_questions", 0)

    result = {
        "players_total": players_in_session,
        "total_joined_players": total_players,
        "kicked_players": kicked_players,
        "eligible_players": players_in_session,
        "players_answered": resolved_players,
        "submitted_answers": responses_to_question,
        "fair_play_resolved": fair_play_resolved_players,
        "waiting_for_players": not ready_for_progression,
        "current_question_index": current_question_index,
        "total_questions": total_questions,
        "game_state": frontend_game_state,
        "currentQuestion": current_question_index + 1,
        "totalQuestions": total_questions,
        "playersCount": players_in_session,
        "playersAnswered": resolved_players,
        "isstarted": game_state.isstarted,
        "is_active": game_state.is_active,
        "ready_for_progression": ready_for_progression,
        "progression_lock_skipped": not ready_for_progression,
    }
    logger.info(
        "PROGRESSION PRECHECK session=%s question=%s total=%s kicked=%s eligible=%s responses=%s fair_play_resolved=%s resolved=%s ready=%s",
        session_code,
        current_question_id,
        total_players,
        kicked_players,
        players_in_session,
        responses_to_question,
        fair_play_resolved_players,
        resolved_players,
        ready_for_progression,
    )
    return result


def updateGameStartStatus(db: Session, session_code: str, is_started: bool) -> None:
    """
    Update the game's started status
    """
    game_state = get_game_session_state(db, session_code)
    if not game_state:
        raise ValueError("Game session not found")

    game_state.isstarted = is_started
    if is_started and not game_state.started_at:
        game_state.started_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()


def check_and_advance_game(
    db: Session, session_code: str, current_question_id: str
) -> dict:
    """
    Check if all players have answered and advance the game if needed
    """
    try:
        # Serialize the last-answer advancement decision across workers.
        game_state = lock_game_session_state_for_update(db, session_code)
        if not game_state:
            raise ValueError("Game state not found")

        locked_question_id = getattr(
            game_state, "current_question_id", current_question_id
        )
        if locked_question_id != current_question_id:
            logger.info(
                "Skipping stale progression check for session=%s incoming_question=%s current_question=%s",
                session_code,
                current_question_id,
                locked_question_id,
            )
            return {
                "players_total": 0,
                "players_answered": 0,
                "waiting_for_players": False,
                "current_question_index": game_state.current_question_index,
                "total_questions": game_state.total_questions,
                "game_state": "active" if game_state.isstarted else "waiting",
                "currentQuestion": game_state.current_question_index + 1,
                "totalQuestions": game_state.total_questions,
                "playersCount": 0,
                "playersAnswered": 0,
                "isstarted": game_state.isstarted,
                "is_active": game_state.is_active,
                "stale_question": True,
            }

        # Get counts from database after the state row lock is held.
        total_players = get_number_of_players_in_session(db, session_code)
        kicked_players = count_kicked_players(db, session_code)
        players_in_session = max(0, total_players - kicked_players)
        responses_to_question = count_responses_for_question(
            db, session_code, current_question_id
        )
        fair_play_resolved_players = count_fair_play_resolved_players_for_question(
            db, session_code, current_question_id
        )
        resolved_players = min(
            players_in_session, responses_to_question + fair_play_resolved_players
        )

        # Determine the appropriate game state for frontend
        frontend_game_state = "active" if game_state.isstarted else "waiting"

        result = {
            "players_total": players_in_session,
            "total_joined_players": total_players,
            "kicked_players": kicked_players,
            "eligible_players": players_in_session,
            "players_answered": resolved_players,
            "submitted_answers": responses_to_question,
            "fair_play_resolved": fair_play_resolved_players,
            "waiting_for_players": resolved_players < players_in_session,
            "current_question_index": game_state.current_question_index,
            "total_questions": game_state.total_questions,
            "game_state": frontend_game_state,
            # Add frontend-compatible format
            "currentQuestion": game_state.current_question_index
            + 1,  # Frontend expects 1-based indexing
            "totalQuestions": game_state.total_questions,
            "playersCount": players_in_session,
            "playersAnswered": resolved_players,
            "isstarted": game_state.isstarted,
            "is_active": game_state.is_active,
        }
        logger.info(
            "PROGRESSION CHECK session=%s question=%s total=%s kicked=%s eligible=%s responses=%s fair_play_resolved=%s resolved=%s",
            session_code,
            current_question_id,
            total_players,
            kicked_players,
            players_in_session,
            responses_to_question,
            fair_play_resolved_players,
            resolved_players,
        )

        # If all players have answered
        if total_players > 0 and resolved_players >= players_in_session:
            logger.info(
                "All players (%s/%s, submitted=%s, fair_play_resolved=%s) have resolved question %s",
                resolved_players,
                players_in_session,
                responses_to_question,
                fair_play_resolved_players,
                current_question_id,
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

                # Update frontend-compatible data after advancement
                if "action" in advancement_result:
                    updated_game_state = get_game_session_state(db, session_code)
                    if updated_game_state:
                        result["currentQuestion"] = (
                            updated_game_state.current_question_index + 1
                        )
                        result["current_question_index"] = (
                            updated_game_state.current_question_index
                        )
                        result["playersAnswered"] = 0  # Reset for new question
            else:
                logger.info(
                    "Game ending. No more questions after index %s",
                    game_state.current_question_index,
                )

                result.update(
                    {
                        "action": "game_ended",
                        "game_state": "ended",
                        "current_question_index": game_state.current_question_index,
                        "total_questions": game_state.total_questions,
                    }
                )
        else:
            logger.info(
                f"Waiting for more players to answer. {responses_to_question}/{players_in_session} have answered"
            )

        logger.info(f"Final check_and_advance_game result: {result}")
        return result
    except Exception as e:
        logger.exception(
            "CHECK AND ADVANCE FAILED session=%s question=%s",
            session_code,
            current_question_id,
        )
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


def build_question_with_randomized_options(question) -> dict:
    """Build randomized display options from an already-loaded question."""
    question_id = getattr(question, "question_id", None) or "unknown"
    try:
        if not question:
            raise ValueError("Question not found")

        raw_options = getattr(question, "question_options", None)

        # Handle questions that might not have options yet
        if not raw_options:
            logger.warning(
                f"Question {question_id} has no question_options, falling back to text input"
            )
            return {
                "question_id": question.question_id,
                "question": question.question,
                "answer": question.answer,
                "genre": question.genre,
                "difficulty": (
                    question.difficulty.value if question.difficulty else "easy"
                ),
                "question_options": [],
                "display_options": [],
                "correct_index": None,
            }

        # Parse and randomize the options with robust error handling
        logger.debug(
            f"Question {question_id} question_options raw value: {repr(raw_options)}"
        )

        incorrect_options = []

        if raw_options:
            # Check if it's already a list (PostgreSQL JSON field) or needs parsing
            if isinstance(raw_options, list):
                # Already parsed by SQLAlchemy
                incorrect_options = raw_options
                logger.debug(
                    f"Question {question_id} options already parsed as list: {incorrect_options}"
                )
            elif isinstance(raw_options, str):
                # String that needs JSON parsing - try multiple parsing approaches
                for attempt, clean_func in enumerate(
                    [
                        lambda x: x,  # Original
                        lambda x: x.strip(),  # Remove whitespace
                        lambda x: x.strip().lstrip("\ufeff"),  # Remove BOM
                        lambda x: x.replace("\x00", ""),  # Remove null bytes
                    ],
                    1,
                ):
                    try:
                        cleaned_options = clean_func(raw_options)
                        incorrect_options = json.loads(cleaned_options)
                        logger.debug(
                            f"Question {question_id} parsed options (attempt {attempt}): {incorrect_options}"
                        )
                        break
                    except (json.JSONDecodeError, TypeError) as e:
                        if attempt == 1:
                            logger.error(
                                f"Question {question_id} JSON parsing failed: {e}"
                            )
                            logger.error(f"Raw value: {repr(raw_options)}")
                        continue
                else:
                    # All parsing attempts failed
                    logger.error(
                        f"Question {question_id} - All JSON parsing attempts failed"
                    )
                    # Fallback to answer only
                    return {
                        "question_id": question.question_id,
                        "question": question.question,
                        "answer": question.answer,
                        "genre": question.genre,
                        "difficulty": (
                            question.difficulty.value if question.difficulty else "easy"
                        ),
                        "question_options": [],
                        "display_options": [],
                        "correct_index": None,
                    }
            else:
                # Unknown type
                logger.error(
                    f"Question {question_id} question_options is unexpected type: {type(raw_options)}"
                )
                return {
                    "question_id": question.question_id,
                    "question": question.question,
                    "answer": question.answer,
                    "genre": question.genre,
                    "difficulty": (
                        question.difficulty.value if question.difficulty else "easy"
                    ),
                    "question_options": [],
                    "display_options": [],
                    "correct_index": None,
                }

        # Combine incorrect options with correct answer
        all_options = incorrect_options + [question.answer]
        random.shuffle(all_options)
        correct_index = all_options.index(question.answer)

        result = {
            "question_id": question.question_id,
            "question": question.question,
            "answer": question.answer,
            "genre": question.genre,
            "difficulty": question.difficulty.value if question.difficulty else "easy",
            "question_options": raw_options if raw_options else [],
            "display_options": all_options,
            "correct_index": correct_index,
        }

        logger.debug(
            "Question %s randomized with %s display options",
            question_id,
            len(result["display_options"]),
        )
        return result

    except Exception as e:
        logger.error("Error getting question with options for %s: %s", question_id, e)
        difficulty = getattr(question, "difficulty", None)
        difficulty_value = getattr(difficulty, "value", difficulty) or "easy"
        # Return a minimal fallback response instead of raising
        return {
            "question_id": question_id,
            "question": getattr(question, "question", "Question unavailable"),
            "answer": getattr(question, "answer", "Unknown"),
            "genre": getattr(question, "genre", "Trivia"),
            "difficulty": difficulty_value,
            "question_options": [],
            "display_options": [],
            "correct_index": None,
        }


def get_question_with_randomized_options(db: Session, question_id: str) -> dict:
    """
    Get a question with randomized multiple choice options
    Returns the same format as the questions route for consistency
    """
    question = get_question_by_id(question_id, db)
    return build_question_with_randomized_options(question)


async def broadcast_question_with_options(
    session_code: str, question_id: str, db: Session
):
    """
    Broadcast a question with randomized display options to all players in a session
    """
    try:
        from app.websockets.manager import SessionPhase, manager

        # Get question with randomized options
        question_data = get_question_with_randomized_options(db, question_id)
        if (
            str(question_data.get("question_id") or question_id)
            .upper()
            .startswith("BTC")
        ):
            logger.warning(
                "Preventing standard question broadcast for Beat the Clock question %s in session %s",
                question_id,
                session_code,
            )
            from app.websockets.game_handlers import create_game_handler
            from app.websockets.game_modes import BEAT_THE_CLOCK_GAME_TYPE
            from app.websockets.manager import manager

            manager.set_session_game_type(session_code, BEAT_THE_CLOCK_GAME_TYPE)
            beat_clock_handler = create_game_handler(
                session_code,
                BEAT_THE_CLOCK_GAME_TYPE,
            )
            await beat_clock_handler.handle_game_start(db)
            return

        # Determine ui_mode based on difficulty
        difficulty = question_data.get("difficulty", "").lower()
        ui_mode = "text_input"  # Default
        if (
            question_data.get("display_options")
            and len(question_data["display_options"]) > 0
        ):
            if difficulty in ["easy", "medium"]:
                ui_mode = "multiple_choice"
            elif difficulty == "hard":
                ui_mode = "text_input"

        game_type = (
            "beat_the_clock"
            if str(question_data.get("question_id") or question_id)
            .upper()
            .startswith("BTC")
            else "trivia"
        )

        # Create message for mobile players (without correct answer info)
        # MUST match the format from TriviaGameHandler.format_question_for_mobile()
        player_message = {
            "type": "question_started",
            "data": {
                "game_type": game_type,  # CRITICAL: Mobile needs this to identify game mode
                "question_id": question_data["question_id"],
                "question": question_data["question"],
                "genre": question_data["genre"],
                "difficulty": question_data["difficulty"],
                "display_options": question_data["display_options"],
                "options": question_data["display_options"],  # Primary field for mobile
                "ui_mode": ui_mode,  # Mobile uses this to determine input type
                "question_index": None,  # Will be added by caller if needed
                "total_questions": None,  # Will be added by caller if needed
            },
        }

        # Create message for web host with display metadata only.
        host_message = {
            "type": "question_started",
            "data": {
                "game_type": game_type,  # Include for consistency
                "question_id": question_data["question_id"],
                "question": question_data["question"],
                "genre": question_data["genre"],
                "difficulty": question_data["difficulty"],
                "display_options": question_data[
                    "display_options"
                ],  # Randomized options for display
                "options": question_data["display_options"],  # Alias for compatibility
                "question_options": question_data[
                    "question_options"
                ],  # All original options
                "ui_mode": ui_mode,  # Include ui_mode for web
                "question_index": None,
                "total_questions": None,
            },
        }
        host_message["data"] = sanitize_question_for_client(host_message["data"])

        logger.info(
            "Broadcasting question %s - option_count=%s ui_mode=%s",
            question_id,
            len(question_data.get("display_options") or []),
            ui_mode,
        )

        # CRITICAL: Queue the question data so mobile clients can retrieve it
        # This ensures questions are never lost even if WebSocket messages are missed
        mobile_question_data = player_message["data"]
        start_at = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
        phase_state = manager.set_session_phase(
            session_code,
            SessionPhase.QUESTION,
            start_at=start_at,
            current_question_id=question_id,
        )
        mobile_question_data["start_at"] = start_at
        host_message["data"]["start_at"] = start_at
        mobile_question_data["phase"] = phase_state["phase"]
        mobile_question_data["server_time_ms"] = phase_state["server_time_ms"]
        host_message["data"]["phase"] = phase_state["phase"]
        host_message["data"]["server_time_ms"] = phase_state["server_time_ms"]
        manager.queue_question(session_code, mobile_question_data)
        logger.info(f"📥 Question {question_id} queued for session {session_code}")

        # Send to mobile players (without answer)
        logger.info(
            f"📱 Sending question_started to MOBILE clients - question_id: {question_data['question_id']}, ui_mode: {ui_mode}, options: {len(question_data['display_options'])} items"
        )
        logger.info(f"📱 MOBILE MESSAGE PAYLOAD: {player_message}")
        await manager.broadcast_to_mobile_players(session_code, player_message)

        # Send to web host with display_options.
        logger.info(
            f"💻 Sending question_started to WEB clients - question_id: {question_data['question_id']}, ui_mode: {ui_mode}, options: {len(question_data['display_options'])} items"
        )
        await manager.broadcast_to_web_clients(session_code, host_message)

        logger.info(
            f"✅ Broadcasted question {question_id} with display_options and ui_mode={ui_mode} to session {session_code}"
        )

    except Exception as e:
        logger.error(f"Failed to broadcast question with options: {e}")
        # Try to send a fallback question instead of just an error
        try:
            fallback_message = {
                "type": "question_started",
                "data": {
                    "game_type": "trivia",  # Include game_type for mobile compatibility
                    "question_id": question_id,
                    "question": "Question temporarily unavailable",
                    "genre": "Trivia",
                    "difficulty": "easy",
                    "display_options": ["Please wait for next question"],
                    "options": ["Please wait for next question"],
                    "ui_mode": "text_input",
                    "question_index": None,
                    "total_questions": None,
                },
            }
            await manager.broadcast_to_session(session_code, fallback_message)
        except:
            # Last resort: send error message
            await manager.broadcast_to_session(
                session_code,
                {"type": "error", "data": {"message": "Failed to load question"}},
            )
