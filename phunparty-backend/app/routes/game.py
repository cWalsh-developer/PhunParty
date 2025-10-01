from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.dbCRUD import create_game as cg, end_game_session
from app.database.dbCRUD import (
    create_game_session,
)
from app.database.dbCRUD import get_all_games as gag
from app.database.dbCRUD import (
    get_all_public_sessions,
    get_game_by_code,
    get_player_private_sessions,
    get_session_by_code,
    get_session_details,
    join_game,
)
from app.dependencies import get_api_key, get_db
from app.models.game import GameCreation, GameJoinRequest, GameSessionCreation
from app.models.game_model import Game
from app.models.response_models import GameResponse

router = APIRouter(dependencies=[Depends(get_api_key)])


@router.post("/", tags=["Game"])
def create_game(request: GameCreation, db: Session = Depends(get_db)):
    try:
        game = cg(db, request.rules, request.genre)
        return {
            "message": "Game created successfully.",
            "game_code": game.game_code,
            "rules": game.rules,
            "genre": game.genre,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create game")


@router.post("/create/session", tags=["Game"])
def create_game_session_route(
    request: GameSessionCreation, db: Session = Depends(get_db)
):
    """
    Create a new game session.
    """
    try:
        gameSession = create_game_session(
            db,
            request.host_name,
            request.number_of_questions,
            request.game_code,
            request.owner_player_id,
            request.ispublic,
        )
        return {
            "session_code": gameSession.session_code,
            "host_name": gameSession.host_name,
            "number_of_questions": gameSession.number_of_questions,
            "game_code": gameSession.game_code,
            "owner_player_id": gameSession.owner_player_id,
            "message": "Game session created successfully",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create game session")


@router.get("/{game_code}", tags=["Game"])
def get_game(game_code: str, db: Session = Depends(get_db)):
    """
    Retrieve the game session details by game code.
    """
    try:
        game = get_game_by_code(db, game_code)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        return game
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve game information"
        )


@router.get("/", response_model=List[GameResponse], tags=["Game"])
def get_all_games(db: Session = Depends(get_db)):
    """
    Retrieve all games.
    """
    try:
        games = gag(db)
        if not games:
            raise HTTPException(status_code=404, detail="No games found")
        return games
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Unable to retrieve games list")


@router.post("/join", tags=["Game"])
def join_game_route(req: GameJoinRequest, db: Session = Depends(get_db)):
    """
    Join an existing game session.
    """
    try:
        game = join_game(db, req.session_code, req.player_id)
        return {
            "message": "Successfully joined the game!",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail="Unable to join game")


@router.get("/session/{session_code}/join-info", tags=["Game"])
def get_session_join_info(session_code: str, db: Session = Depends(get_db)):
    """
    Get session join information for WebSocket connection.
    """
    try:
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve session information"
        )


@router.get("/session/{session_code}/details", tags=["Game"])
def get_session_details_route(session_code: str, db: Session = Depends(get_db)):
    """
    Get comprehensive session information including session code, genre,
    number of questions, active status, and privacy status.
    """
    try:
        session_details = get_session_details(db, session_code)
        if not session_details:
            raise HTTPException(status_code=404, detail="Session not found")
        return session_details
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve session details"
        )


@router.get("/sessions/public", tags=["Game"])
def get_all_public_sessions_route(db: Session = Depends(get_db)):
    """
    Get all public active sessions (available to everyone).
    Returns: session_code, genre, number_of_questions, difficulty
    """
    try:
        sessions = get_all_public_sessions(db)
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve public sessions"
        )


@router.get("/sessions/private/{player_id}", tags=["Game"])
def get_player_private_sessions_route(player_id: str, db: Session = Depends(get_db)):
    """
    Get all private active sessions owned by a specific player.
    Returns: session_code, genre, number_of_questions, difficulty
    """
    try:
        sessions = get_player_private_sessions(db, player_id)
        return {"player_id": player_id, "sessions": sessions, "count": len(sessions)}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve private sessions"
        )


@router.post("/end-game/{session_code}", tags=["Game"])
def end_game_route(session_code: str, db: Session = Depends(get_db)):
    """
    End a game session.
    """
    try:
        result = end_game_session(db, session_code)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to end game session")
