from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.models.game_model import Game
from app.models.players_model import Players
import string, random

# Game CRUD operations -----------------------------------------------------------------------------------------------------
def generate_game_code(length=6):
    """Generate a random game code consisting of uppercase letters and digits."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def create_game(db: Session, host_name: str, rules: str, genre: str, players: list = [], scores: dict = {}) -> Game:
    """Create a new game session in the database."""
    game_code = generate_game_code()
    new_game = Game(game_code=game_code, 
                    host_name=host_name, 
                    rules=rules,
                    genre=genre,
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

def join_game(db: Session, game_code: str, player_name: str, player_id: str) -> Game:
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
        update_player_game_code(db, player_id, game_code)
        return game
    
# Players CRUD operations -----------------------------------------------------------------------------------------------------
def create_player_id() -> str:
    """Generate a unique player ID."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def get_player_by_ID(db: Session, player_ID: str) -> Players:
    """Retrieve a player by their ID."""
    return db.query(Players).filter(Players.player_id == player_ID).first()

def get_all_players(db: Session) -> list[Players]:
    """Retrieve all players."""
    return db.query(Players).all()

def create_player(db: Session, player_name: str, player_email: str, player_mobile: str, game_code: str = None) -> Players:
    """Create a new player and add them to a game."""
    player_id = create_player_id()
    new_player = Players(player_id=player_id, 
                         player_name=player_name, 
                         player_email=player_email,
                         player_mobile=player_mobile,
                         game_code=game_code)
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
    player.game_code = game_code
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
    if player.game_code is not None:
        raise ValueError("Cannot update player name while they are in a game")
    else:
        player.player_name = player_name
        db.commit()
        db.refresh(player)
    return player
    # History CRUD operations -----------------------------------------------------------------------------------------------------
    # Questions CRUD operations -----------------------------------------------------------------------------------------------------