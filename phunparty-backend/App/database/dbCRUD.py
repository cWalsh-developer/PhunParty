from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.models.db_model import Game
import string, random

def generate_game_code(length=6):
    """Generate a random game code consisting of uppercase letters and digits."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def create_game(db: Session, host_name: str, players: list = [], scores: dict = {}) -> Game:
    """Create a new game session in the database."""
    game_code = generate_game_code()
    new_game = Game(game_code=game_code, 
                    host_name=host_name, 
                    players= players, 
                    scores=scores)
    db.add(new_game)
    db.commit()
    db.refresh(new_game)
    return new_game

def get_game_by_code(db: Session, game_code: str) -> Game:
    """Retrieve a game session by its game code."""
    return db.query(Game).filter(Game.game_code == game_code).first()

def get_all_games(db: Session) -> list[Game]:
    """Retrieve all game sessions."""
    return db.query(Game).all()

def join_game(db: Session, game_code: str, player_name: str) -> Game:
    """Join an existing game session."""
    game = get_game_by_code(db, game_code)
    if not game:
        raise ValueError("Game not found")
    
    if game.players is None:
        game.players = []

    if game.scores is None:
        game.scores = {}
 
    if player_name in game.players:
        raise ValueError("Player already in the game")
    else:
        game.players.append(player_name)
        game.scores[player_name] = 0
        flag_modified(game, "scores")
        flag_modified(game, "players")
        db.commit()
        db.refresh(game)
        return game