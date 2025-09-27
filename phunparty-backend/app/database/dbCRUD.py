from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.game_model import Game
from app.models.game_session_model import GameSession
from app.models.game_state_models import GameSessionState, PlayerResponse
from app.models.passwordReset import PasswordReset
from app.models.players import Player
from app.models.players_model import Players
from app.models.questions_model import Questions
from app.models.scores_model import Scores
from app.models.session_player_assignment_model import SessionAssignment
from app.models.session_question_assignment import SessionQuestionAssignment
from app.utils.hash_password import hash_password
from app.utils.id_generator import (
    generate_assignment_id,
    generate_game_code,
    generate_player_id,
    generate_question_id,
    generate_response_id,
    generate_score_id,
    generate_session_code,
)


def create_game(db: Session, rules: str, genre: str) -> Game:
    """Create a new game session in the database."""
    game_code = generate_game_code()
    new_game = Game(game_code=game_code, rules=rules, genre=genre)
    db.add(new_game)
    db.commit()
    db.refresh(new_game)
    return new_game


def create_game_session(
    db: Session,
    host_name: str,
    number_of_questions: int,
    game_code: str,
    owner_player_id: str = None,
) -> GameSession:
    """Create a new game session with the specified parameters."""
    session_code = generate_session_code()
    gameSession = GameSession(
        session_code=session_code,
        host_name=host_name,
        number_of_questions=number_of_questions,
        game_code=game_code,
        owner_player_id=owner_player_id,
    )
    db.add(gameSession)
    db.commit()
    db.refresh(gameSession)
    if not gameSession:
        raise ValueError("Failed to create game session")
    add_question_to_session(db, session_code)

    # Initialize game state tracking
    try:
        create_game_session_state(db, session_code)
    except Exception as e:
        print(f"Warning: Could not initialize game state: {e}")

    return gameSession


def get_session_by_code(db: Session, session_code: str) -> GameSession:
    """Retrieve a game session by its session code."""
    return (
        db.query(GameSession).filter(GameSession.session_code == session_code).first()
    )


def get_game_by_code(db: Session, game_code: str) -> Game:
    """Retrieve a game session by its game code."""
    return db.query(Game).filter(Game.game_code == game_code).first()


def get_all_games(db: Session) -> list[Game]:
    """Retrieve all game sessions."""
    return db.query(Game).all()


def join_game(db: Session, session_code: str, player_id: str) -> GameSession:
    """Join an existing game session."""
    gameSession = get_session_by_code(db, session_code)
    if not gameSession:
        raise ValueError("Game session not found")
    player = get_player_by_ID(db, player_id)
    if player.active_game_code is not None:
        raise ValueError("Player is already in a game session")
    update_player_game_code(db, player_id, gameSession.session_code)
    assign_player_to_session(db, player_id, session_code)
    create_score(db, session_code, player_id)
    return gameSession


def end_game_session(db: Session, session_code: str) -> dict:
    """End a game session, reset player codes, calculate results, and update game state"""
    gameSession = get_session_by_code(db, session_code)
    if not gameSession:
        raise ValueError("Game session not found")

    # Reset active game code for players
    players = db.query(Players).filter(Players.active_game_code == session_code).all()
    session_assignments = (
        db.query(SessionAssignment)
        .filter(SessionAssignment.session_code == session_code)
        .all()
    )

    # Update session assignment end times
    for assignment in session_assignments:
        if not assignment.session_end:
            assignment.session_end = datetime.now()

    # Reset player game codes
    for player in players:
        player.active_game_code = None

    # Update game state if it exists
    game_state = get_game_session_state(db, session_code)
    if game_state:
        game_state.is_active = False
        game_state.is_waiting_for_players = False
        game_state.ended_at = datetime.utcnow()

    db.commit()

    # Calculate and return final results
    try:
        final_results = calculate_game_results(db, session_code)
        return {
            "action": "game_ended",
            "game_status": "completed",
            "final_results": [
                {
                    "player_id": result.player_id,
                    "score": result.score,
                    "result": result.result,
                }
                for result in final_results
            ],
        }
    except Exception as e:
        return {
            "action": "game_ended",
            "game_status": "completed",
            "error": f"Could not calculate final results: {str(e)}",
        }


# Players CRUD operations -----------------------------------------------------------------------------------------------------


def get_player_by_ID(db: Session, player_ID: str) -> Players:
    """Retrieve a player by their ID."""
    return db.query(Players).filter(Players.player_id == player_ID).first()


def get_all_players(db: Session) -> list[Players]:
    """Retrieve all players."""
    return db.query(Players).all()


def get_player_by_email(db: Session, player_email: str) -> Players:
    """Retrieve a player by their email."""
    return db.query(Players).filter(Players.player_email == player_email).first()


def create_player(
    db: Session,
    player_name: str,
    player_email: str,
    player_mobile: str,
    hashed_password: str,
    game_code: str = None,
) -> Players:
    """Create a new player and add them to a game."""
    player_id = generate_player_id()
    hashed_password = hash_password(hashed_password)
    new_player = Players(
        player_id=player_id,
        player_name=player_name,
        player_email=player_email,
        player_mobile=player_mobile,
        hashed_password=hashed_password,
        active_game_code=game_code,
    )
    db.add(new_player)
    db.commit()
    db.refresh(new_player)
    return new_player


def update_player_game_code(db: Session, player_id: str, game_code: str) -> Players:
    """Update the game code of a player."""
    player = get_player_by_ID(db, player_id)
    player.active_game_code = game_code
    db.commit()
    db.refresh(player)
    return player


def delete_player(db: Session, player_id: str) -> None:
    """Delete a player from the database."""
    player = get_player_by_ID(db, player_id)
    db.delete(player)
    db.commit()


def update_player(db: Session, player_id: str, player: Player) -> Players:
    """Update the name of a player."""
    new_player = get_player_by_ID(db, player_id)
    if new_player.active_game_code is not None:
        raise ValueError("Cannot update player name while they are in a game")
    else:
        new_player.player_name = player.player_name
        new_player.player_email = player.player_email
        new_player.player_mobile = player.player_mobile
        if (
            hasattr(player, "profile_photo_url")
            and player.profile_photo_url is not None
        ):
            new_player.profile_photo_url = player.profile_photo_url
        db.commit()
        db.refresh(new_player)
    return new_player


def update_player_photo(db: Session, player_id: str, photo_url: str = None) -> Players:
    """Update a player's profile photo URL."""
    player = get_player_by_ID(db, player_id)
    if not player:
        raise ValueError("Player not found")

    player.profile_photo_url = photo_url
    db.commit()
    db.refresh(player)
    return player


def get_number_of_players_in_session(db: Session, session_code: str) -> int:
    """Retrieve the number of players in a specific game session."""
    session = get_session_by_code(db, session_code)
    if not session:
        raise ValueError("Session not found")
    return (
        db.query(SessionAssignment)
        .filter(SessionAssignment.session_code == session_code)
        .count()
    )

    # Session Player Assignment CRUD operations -----------------------------------------------------------------------------------------------------


def assign_player_to_session(db: Session, player_id: str, session_code: str) -> None:
    """Assign a player to a game session."""
    assignment_id = generate_assignment_id()
    assignment = SessionAssignment(
        assignment_id=assignment_id,
        player_id=player_id,
        session_code=session_code,
        session_start=datetime.now(),
        session_end=None,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    # Session Questions Assignment CRUD operations --------------------------------------------------------------------------------------------------------------


def add_question_to_session(db: Session, session_code: str) -> None:
    """Add a question to a game session."""
    session = get_session_by_code(db, session_code)
    if not session:
        raise ValueError("Session not found")
    game = get_game_by_code(db, session.game_code)
    if not game:
        raise ValueError("Game not found")
    questions = (
        db.query(Questions)
        .filter(Questions.genre == game.genre)
        .limit(session.number_of_questions)
        .all()
    )
    if not questions:
        raise ValueError("No questions available for this game genre")
    for question in questions:
        assignment = SessionQuestionAssignment(
            assignment_id=generate_question_id(),
            question_id=question.question_id,
            session_code=session_code,
        )
        db.add(assignment)
        db.commit()
        db.refresh(assignment)


def submit_questions(db: Session, question: Questions) -> Questions:
    """Submit a question to the database."""
    # Only generate a new ID if one wasn't provided
    if not question.question_id:
        question.question_id = generate_question_id()
    db.add(question)
    db.commit()
    db.refresh(question)
    return question

    # Questions CRUD operations --------------------------------------------------------------------------------------------------------------


def get_questions_by_session_code(session_code: str, db: Session) -> list[Questions]:
    """Retrieve questions for a specific game session."""
    questions = (
        db.query(Questions)
        .join(
            SessionQuestionAssignment,
            Questions.question_id == SessionQuestionAssignment.question_id,
        )
        .filter(SessionQuestionAssignment.session_code == session_code)
        .all()
    )
    if not questions:
        raise ValueError("No questions found for this session")
    return questions


def get_question_by_id(question_id: str, db: Session) -> Questions:
    """Retrieve a question by its ID."""
    question = db.query(Questions).filter(Questions.question_id == question_id).first()
    return question


def retrieve_number_of_questions_value(db: Session, session_code: str) -> int:
    """Retrieve the number of questions for a specific game session."""
    session = get_session_by_code(db, session_code)
    if not session:
        raise ValueError("Session not found")
    return session.number_of_questions

    # Scores CRUD operations -----------------------------------------------------------------------------------------------------------------


def get_scores_by_session_and_player(db: Session, session_code: str, player_id: str):
    """Retrieve scores for a specific player in a game session."""
    game_session = get_session_by_code(db, session_code)
    if not game_session:
        raise ValueError("Game session not found")
    player = get_player_by_ID(db, player_id)
    if not player:
        raise ValueError("Player not found")
    scores = db.query(Scores).filter(
        Scores.session_code == session_code, Scores.player_id == player_id
    )
    return scores.first() if scores else None


def update_scores(db: Session, session_code: str, player_id: str) -> Scores:
    """Update the scores for a specific player in a game session."""
    existing_score = get_scores_by_session_and_player(db, session_code, player_id)
    existing_score.score += 1
    db.commit()
    db.refresh(existing_score)
    return existing_score


def create_score(db: Session, session_code: str, player_id: str) -> Scores:
    """Create a new score entry for a player in a game session."""
    score_id = generate_score_id()
    new_score = Scores(
        score_id=score_id, session_code=session_code, player_id=player_id, score=0
    )
    db.add(new_score)
    db.commit()
    db.refresh(new_score)
    return new_score


def get_scores_by_session(db: Session, session_code: str) -> list[Scores]:
    """Retrieve scores for a specific game session."""
    scores = db.query(Scores).filter(Scores.session_code == session_code).all()
    if not scores:
        raise ValueError("No scores found for this session")
    return scores


def calculate_game_results(db: Session, session_code: str):
    """Determine the game results (win, lose, draw) for a session and update the DB."""

    session_scores = db.query(Scores).filter(Scores.session_code == session_code).all()
    if not session_scores:
        raise ValueError("No scores found for session")

    max_score = max(score.score for score in session_scores)

    top_scorers = [s for s in session_scores if s.score == max_score]

    for s in session_scores:
        if s.score == max_score:
            s.result = "draw" if len(top_scorers) > 1 else "win"
        else:
            s.result = "lose"
    db.commit()
    db.refresh(session_scores)
    return session_scores


# Game State Management CRUD operations -----------------------------------------------------------------------------------------------------------------


def create_game_session_state(db: Session, session_code: str) -> GameSessionState:
    """Initialize the game state when a session is created"""
    session = get_session_by_code(db, session_code)
    if not session:
        raise ValueError("Session not found")

    # Get the first question for this session
    first_question = (
        db.query(SessionQuestionAssignment)
        .filter(SessionQuestionAssignment.session_code == session_code)
        .first()
    )

    game_state = GameSessionState(
        session_code=session_code,
        current_question_index=0,
        current_question_id=first_question.question_id if first_question else None,
        is_active=True,
        is_waiting_for_players=True,
        total_questions=session.number_of_questions,
    )

    db.add(game_state)
    db.commit()
    db.refresh(game_state)
    return game_state


def get_game_session_state(db: Session, session_code: str) -> GameSessionState:
    """Get current game state for a session"""
    return (
        db.query(GameSessionState)
        .filter(GameSessionState.session_code == session_code)
        .first()
    )


def get_session_details(db: Session, session_code: str) -> dict:
    """
    Get comprehensive session information including session code, genre,
    number of questions, active status, and privacy status.
    """
    # Get the session
    session = get_session_by_code(db, session_code)
    if not session:
        return None

    # Get the game to get the genre
    game = get_game_by_code(db, session.game_code)
    if not game:
        return None

    # Get the game state for active/inactive and public/private status
    game_state = get_game_session_state(db, session_code)

    # Default values if game state doesn't exist
    is_active = True
    is_public = True
    started_at = None
    ended_at = None

    if game_state:
        is_active = game_state.is_active
        is_public = game_state.is_public
        started_at = game_state.started_at
        ended_at = game_state.ended_at

    return {
        "session_code": session.session_code,
        "host_name": session.host_name,
        "game_code": session.game_code,
        "genre": game.genre,
        "number_of_questions": session.number_of_questions,
        "is_active": is_active,
        "is_public": is_public,
        "created_at": started_at,
        "ended_at": ended_at,
    }


def get_all_public_sessions(db: Session) -> list:
    """
    Get all public active sessions (available to everyone).
    Returns basic session info: session_code, genre, number_of_questions, difficulty
    """
    sessions = (
        db.query(GameSession, Game, GameSessionState)
        .join(Game, GameSession.game_code == Game.game_code)
        .join(
            GameSessionState, GameSession.session_code == GameSessionState.session_code
        )
        .filter(GameSessionState.is_public == True)
        .filter(GameSessionState.is_active == True)
        .all()
    )

    result = []
    for session, game, state in sessions:
        # Get difficulty from questions assigned to this session
        difficulty = get_session_difficulty(db, session.session_code)

        result.append(
            {
                "session_code": session.session_code,
                "genre": game.genre,
                "number_of_questions": session.number_of_questions,
                "difficulty": difficulty,
            }
        )

    return result


def get_player_private_sessions(db: Session, player_id: str) -> list:
    """
    Get all private active sessions owned by a specific player.
    Returns basic session info: session_code, genre, number_of_questions, difficulty
    """
    sessions = (
        db.query(GameSession, Game, GameSessionState)
        .join(Game, GameSession.game_code == Game.game_code)
        .join(
            GameSessionState, GameSession.session_code == GameSessionState.session_code
        )
        .filter(GameSession.owner_player_id == player_id)
        .filter(GameSessionState.is_public == False)
        .filter(GameSessionState.is_active == True)
        .all()
    )

    result = []
    for session, game, state in sessions:
        # Get difficulty from questions assigned to this session
        difficulty = get_session_difficulty(db, session.session_code)

        result.append(
            {
                "session_code": session.session_code,
                "genre": game.genre,
                "number_of_questions": session.number_of_questions,
                "difficulty": difficulty,
            }
        )

    return result


def get_session_difficulty(db: Session, session_code: str) -> str:
    """Get the difficulty level from questions assigned to a session"""
    try:
        # Get the first question assigned to this session to determine difficulty
        difficulty = (
            db.query(Questions.difficulty)
            .join(
                SessionQuestionAssignment,
                Questions.question_id == SessionQuestionAssignment.question_id,
            )
            .filter(SessionQuestionAssignment.session_code == session_code)
            .first()
        )

        if difficulty:
            return difficulty[0].value  # Extract the enum value
        return "easy"  # Default fallback
    except Exception:
        return "easy"  # Default fallback


def get_session_player_count(db: Session, session_code: str) -> int:
    """Get the number of players currently in a session"""
    try:
        count = (
            db.query(SessionAssignment)
            .filter(SessionAssignment.session_code == session_code)
            .count()
        )
        return count
    except Exception:
        return 0


def create_player_response(
    db: Session,
    session_code: str,
    player_id: str,
    question_id: str,
    player_answer: str,
    is_correct: bool,
) -> PlayerResponse:
    """Create a new player response record"""
    response = PlayerResponse(
        response_id=generate_response_id(),
        session_code=session_code,
        player_id=player_id,
        question_id=question_id,
        player_answer=player_answer,
        is_correct=is_correct,
    )

    db.add(response)
    db.commit()
    db.refresh(response)
    return response


def get_player_response(
    db: Session, session_code: str, player_id: str, question_id: str
) -> PlayerResponse:
    """Check if a player has already answered a specific question"""
    return (
        db.query(PlayerResponse)
        .filter(
            PlayerResponse.session_code == session_code,
            PlayerResponse.player_id == player_id,
            PlayerResponse.question_id == question_id,
        )
        .first()
    )


def count_responses_for_question(
    db: Session, session_code: str, question_id: str
) -> int:
    """Count how many players have answered a specific question"""
    return (
        db.query(PlayerResponse)
        .filter(
            PlayerResponse.session_code == session_code,
            PlayerResponse.question_id == question_id,
        )
        .count()
    )


def get_session_questions_ordered(
    db: Session, session_code: str
) -> list[SessionQuestionAssignment]:
    """Get all questions for a session in order"""
    return (
        db.query(SessionQuestionAssignment)
        .filter(SessionQuestionAssignment.session_code == session_code)
        .all()
    )


def advance_to_next_question(db: Session, session_code: str) -> dict:
    """Advance game to next question or end if no more questions"""
    game_state = get_game_session_state(db, session_code)
    if not game_state:
        raise ValueError("Game state not found")

    questions_in_session = get_session_questions_ordered(db, session_code)
    next_question_index = game_state.current_question_index + 1

    if next_question_index < len(questions_in_session):
        # Move to next question
        next_question = questions_in_session[next_question_index]
        game_state.current_question_index = next_question_index
        game_state.current_question_id = next_question.question_id
        game_state.is_waiting_for_players = True

        db.commit()
        db.refresh(game_state)

        return {
            "action": "next_question",
            "next_question_id": next_question.question_id,
            "current_question_index": next_question_index,
            "waiting_for_players": True,
        }
    else:
        # No more questions, end the game using the comprehensive end_game_session
        return end_game_session(db, session_code)


def update_game_state_waiting_status(
    db: Session, session_code: str, is_waiting: bool
) -> None:
    """Update the waiting for players status"""
    game_state = get_game_session_state(db, session_code)
    if game_state:
        game_state.is_waiting_for_players = is_waiting
        db.commit()


def get_current_question_details(db: Session, session_code: str) -> dict:
    """Get current question details for a session"""
    game_state = get_game_session_state(db, session_code)
    if not game_state:
        return {"error": "Game session not found"}

    current_question = None
    if game_state.current_question_id:
        current_question = get_question_by_id(game_state.current_question_id, db)

    # Get player counts
    total_players = get_number_of_players_in_session(db, session_code)

    players_answered = 0
    if game_state.current_question_id:
        players_answered = count_responses_for_question(
            db, session_code, game_state.current_question_id
        )

    return {
        "session_code": session_code,
        "is_active": game_state.is_active,
        "is_waiting_for_players": game_state.is_waiting_for_players,
        "current_question_index": game_state.current_question_index,
        "total_questions": game_state.total_questions,
        "current_question": {
            "question_id": current_question.question_id if current_question else None,
            "question": current_question.question if current_question else None,
            "genre": current_question.genre if current_question else None,
        },
        "players": {
            "total": total_players,
            "answered": players_answered,
            "waiting_for": total_players - players_answered,
        },
        "started_at": (
            game_state.started_at.isoformat() if game_state.started_at else None
        ),
        "ended_at": game_state.ended_at.isoformat() if game_state.ended_at else None,
    }


def get_player_by_phone(db: Session, phone: str) -> Players:
    """Retrieve a player by their phone number."""
    return db.query(Players).filter(Players.player_mobile == phone).first()


## Password Reset CRUD operations --------------------------------------------------------------------------------------------------------------


def store_otp(db: Session, phone: str, otp: str, expires_at: datetime):
    record = PasswordReset(mobile=phone, code=otp, expires_at=expires_at)
    db.add(record)
    db.commit()
    return record


def verify_otp(db: Session, phone: str, otp: str) -> bool:
    record = (
        db.query(PasswordReset)
        .filter(
            PasswordReset.mobile == phone,
            PasswordReset.code == otp,
            PasswordReset.used == False,
            PasswordReset.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if record:
        record.used = True
        db.commit()
        return True
    return False


def delete_expired_otps(db: Session):
    db.query(PasswordReset).filter(
        PasswordReset.expires_at < datetime.now(timezone.utc)
    ).delete()
    db.commit()


def verify_and_reset_password(
    db: Session, phone: str, otp: str, new_password: str
) -> bool:
    if verify_otp(db, phone, otp):
        player = db.query(Players).filter(Players.player_mobile == phone).first()
        if player:
            player.hashed_password = hash_password(new_password)
            db.commit()
            return True
    return False


def update_password(db: Session, phone: str, new_password: str) -> bool:
    player = db.query(Players).filter(Players.player_mobile == phone).first()
    if player:
        player.hashed_password = hash_password(new_password)
        db.commit()
        return True
    return False
