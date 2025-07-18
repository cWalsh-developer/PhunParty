from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.models.game_model import Game
from app.models.game_session_model import GameSession
from app.models.session_player_assignment_model import SessionAssignment
from app.models.session_question_assignment import SessionQuestionAssignment
from app.models.players_model import Players
from app.models.questions_model import Questions
from app.models.scores_model import Scores
import string, random
from datetime import datetime


# Game CRUD operations -----------------------------------------------------------------------------------------------------
def generate_game_code(length=9):
    """Generate a random game code consisting of uppercase letters and digits."""
    characters = string.ascii_uppercase + string.digits
    return "".join(random.choice(characters) for _ in range(length))


def create_game(db: Session, rules: str, genre: str) -> Game:
    """Create a new game session in the database."""
    game_code = generate_game_code()
    new_game = Game(game_code=game_code, rules=rules, genre=genre)
    db.add(new_game)
    db.commit()
    db.refresh(new_game)
    return new_game


def create_game_session(
    db: Session, host_name: str, number_of_questions: int, game_code: str
) -> GameSession:
    """Create a new game session with the specified parameters."""
    session_code = generate_game_code()
    gameSession = GameSession(
        session_code=session_code,
        host_name=host_name,
        number_of_questions=number_of_questions,
        game_code=game_code,
    )
    db.add(gameSession)
    db.commit()
    db.refresh(gameSession)
    if not gameSession:
        raise ValueError("Failed to create game session")
    add_question_to_session(db, session_code)
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
    update_player_game_code(db, player_id, gameSession.session_code)
    assign_player_to_session(db, player_id, session_code)
    create_score(db, session_code, player_id)
    return gameSession


def end_game_session(db: Session, session_code: str) -> None:
    """End a game session and reset the active game code for players. And calculate the results"""
    gameSession = get_session_by_code(db, session_code)
    if not gameSession:
        raise ValueError("Game session not found")

    # Reset active game code for players
    players = db.query(Players).filter(Players.active_game_code == session_code).all()
    session_end_time = (
        db.query(SessionAssignment)
        .filter(SessionAssignment.session_code == session_code)
        .first()
    )
    if not session_end_time:
        raise ValueError("Session end time not found")
    session_end_time.session_end = datetime.now()
    for player in players:
        player.active_game_code = None
        db.commit()


# Players CRUD operations -----------------------------------------------------------------------------------------------------


def create_player_id() -> str:
    """Generate a unique player ID."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def get_player_by_ID(db: Session, player_ID: str) -> Players:
    """Retrieve a player by their ID."""
    return db.query(Players).filter(Players.player_id == player_ID).first()


def get_all_players(db: Session) -> list[Players]:
    """Retrieve all players."""
    return db.query(Players).all()


def create_player(
    db: Session,
    player_name: str,
    player_email: str,
    player_mobile: str,
    game_code: str = None,
) -> Players:
    """Create a new player and add them to a game."""
    player_id = create_player_id()
    new_player = Players(
        player_id=player_id,
        player_name=player_name,
        player_email=player_email,
        player_mobile=player_mobile,
        active_game_code=game_code,
    )
    db.add(new_player)
    db.commit()
    db.refresh(new_player)
    return new_player


def update_player_score(db: Session, player_id: str, score: int) -> Players:
    """Update the score of a player."""
    player = get_player_by_ID(db, player_id)
    game = get_game_by_code(db, player.game_code)
    if not game:
        raise ValueError("Game not found")
    game.scores[player.player_name] = score
    flag_modified(game, "scores")
    db.commit()
    db.refresh(game)
    return player


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


def update_player_name(db: Session, player_id: str, player_name: str) -> Players:
    """Update the name of a player."""
    player = get_player_by_ID(db, player_id)
    if player.active_game_code is not None:
        raise ValueError("Cannot update player name while they are in a game")
    else:
        player.player_name = player_name
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
    assignment_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
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


def generate_question_id():
    """Generate a unique question ID."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


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
    score_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
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
