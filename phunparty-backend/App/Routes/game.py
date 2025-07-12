from app.database.dbCRUD import create_game as cg, get_game_by_code, get_all_games as gag, join_game, create_game_session
from app.dependencies import get_db
from app.models.response_models import GameResponse
from app.models.game_model import Game
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from app.models.game import GameCreation, GameSessionCreation, GameJoinRequest
from sqlalchemy.orm import Session


router = APIRouter()

@router.post("/create/game", tags=["Game"])
def create_game(request: GameCreation, db: Session = Depends(get_db)):
  game = cg(db, request.rules, request.genre)
  return {"message": "Game created successfully.",
          "game_code": game.game_code,
          "rules": game.rules,
          "genre": game.genre,}

@router.post("/create/session", tags=["Game"])
def create_game_session(request: GameSessionCreation, db: Session = Depends(get_db)):
    """
    Create a new game session.
    """
    gameSession = create_game_session(db, request.host_name, request.number_of_questions, request.game_code)
    return {
        "message": "Game session created successfully.",
        "session_code": gameSession.session_code,
        "host_name": gameSession.host_name,
        "number_of_questions": gameSession.number_of_questions,
        "game_code": gameSession.game_code
    }

@router.get("/{game_code}", tags=["Game"])
def get_game(game_code: str, db: Session = Depends(get_db)):
    """
    Retrieve the game session details by game code.
    """
    game = get_game_by_code(db, game_code)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")   
    return game
       

@router.get("/", response_model= List[GameResponse], tags=["Game"])
def get_all_games(db: Session = Depends(get_db)):
    """
    Retrieve all games.
    """
    games = gag(db)
    if not games:
        raise HTTPException(status_code=404, detail="No games found")
    return games
    
@router.post("/join", tags=["Game"])
def join_game_route(req: GameJoinRequest, db: Session = Depends(get_db)):
    """
    Join an existing game session.
    """
    try:
        game = join_game(db, req.game_code, req.player_id)
        return {
            "message": f"{req.player_id} joined the game successfully.",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

