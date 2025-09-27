from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import create_game as cg
from app.database.dbCRUD import create_game_session
from app.database.dbCRUD import get_all_games as gag
from app.database.dbCRUD import get_game_by_code, get_session_by_code, join_game
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
    return {
        "session_code": gameSession.session_code,
        "host_name": gameSession.host_name,
        "number_of_questions": gameSession.number_of_questions,
        "game_code": gameSession.game_code,
        "message": "Game session created successfully",
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


@router.get("/session/{session_code}/join-info", tags=["Game"])
def get_session_join_info(session_code: str, db: Session = Depends(get_db)):
    """
    Get session join information for WebSocket connection.
    """
    import os

    # Verify session exists
    session = get_session_by_code(db, session_code)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get API URL from environment variable (defaults to production)
    api_url = os.getenv("API_URL", "https://api.phun.party")
    web_url = os.getenv("WEB_URL", "https://phun.party")

    # Convert https to wss for WebSocket URL
    ws_url = api_url.replace("https://", "wss://").replace("http://", "ws://")

    return {
        "session_code": session_code,
        "host_name": session.host_name,
        "game_code": session.game_code,
        "number_of_questions": session.number_of_questions,
        "websocket_url": f"{ws_url}/ws/session/{session_code}",
        "web_join_url": f"{web_url}/#/join/{session_code}",
    }
