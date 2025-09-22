from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import create_game as cg
from app.database.dbCRUD import create_game_session
from app.database.dbCRUD import get_all_games as gag
from app.database.dbCRUD import get_game_by_code, join_game
from app.dependencies import get_api_key, get_db
from app.models.game import GameCreation, GameJoinRequest, GameSessionCreation
from app.models.game_model import Game
from app.models.response_models import GameResponse

router = APIRouter(dependencies=[Depends(get_api_key)])


@router.post("/", tags=["Game"])
def create_game(request: GameCreation, db: Session = Depends(get_db)):
    game = cg(db, request.rules, request.genre)
    return {
        "message": "Game created successfully.",
        "game_code": game.game_code,
        "rules": game.rules,
        "genre": game.genre,
    }


@router.post("/create/session", tags=["Game"])
def create_game_session_route(
    request: GameSessionCreation, db: Session = Depends(get_db)
):
    """
    Create a new game session.
    """
    gameSession = create_game_session(
        db, request.host_name, request.number_of_questions, request.game_code
    )
    return gameSession


@router.get("/{game_code}", tags=["Game"])
def get_game(game_code: str, db: Session = Depends(get_db)):
    """
    Retrieve the game session details by game code.
    """
    game = get_game_by_code(db, game_code)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@router.get("/", response_model=List[GameResponse], tags=["Game"])
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
        game = join_game(db, req.session_code, req.player_id)
        return {
            "message": f"{req.player_id} joined the {game.session_code} successfully.",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
