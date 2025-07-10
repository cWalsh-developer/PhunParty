from app.database.dbCRUD import create_game as cg, get_game_by_code, get_all_games as gag, join_game
from app.dependencies import get_db
from app.models.response_models import GameResponse
from app.models.db_model import Game
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from app.models.game import GameCreation, GameJoinRequest
from app.Logic.session_manager import SessionManager
from sqlalchemy.orm import Session


router = APIRouter()

@router.post("/create", tags=["Game"])
def create_game(request: GameCreation, db: Session = Depends(get_db)):
  game = cg(db, request.host_name, request.players, request.scores)
  return {"message": "Game created successfully.",
          "game_code": game.game_code,
          "host_name": game.host_name,
          "players": game.players,
          "scores": game.scores}

@router.get("/{game_code}", tags=["Game"])
def get_game(game_code: str, db: Session = Depends(get_db)):
    """
    Retrieve the game session details by game code.
    """
    game = db.query(Game).filter(Game.game_code == game_code).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")   
    return game
       

@router.get("/", response_model= List[GameResponse], tags=["Game"])
def get_all_games(db: Session = Depends(get_db)):
    """
    Retrieve all games.
    """
    games = db.query(Game).all()
    if not games:
        raise HTTPException(status_code=404, detail="No games found")
    return games
    
@router.post("/join", tags=["Game"])
def join_game_route(req: GameJoinRequest, db: Session = Depends(get_db)):
    """
    Join an existing game session.
    """
    try:
        game = join_game(db, req.game_code, req.player_name)
        return {
            "message": f"{req.player_name} joined the game successfully.",
            "game_code": game.game_code,
            "host_name": game.host_name,
            "players": game.players
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

