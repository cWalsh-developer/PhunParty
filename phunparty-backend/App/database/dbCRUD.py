from sqlalchemy.orm import Session
from models.db_model import Game
import string, random

def generate_game_code(length=6):
    """Generate a random game code consisting of uppercase letters and digits."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def create_game(db: Session, host_name: str, players: list = [], scores: dict = {}) -> Game:
    """Create a new game session in the database."""
    game_code = generate_game_code()
    new_game = Game(game_code=game_code, host_name=host_name, players=str(players), scores=scores)
    db.add(new_game)
    db.commit()
    db.refresh(new_game)
    return new_game