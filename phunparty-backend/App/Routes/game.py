from fastapi import APIRouter, HTTPException
from App.Models.game import GameCreation
from App.Logic.session_manager import SessionManager


router = APIRouter()
session_manager = SessionManager()
@router.post("/create", tags=["Game"])
def create_game(request: GameCreation):
    """
    Create a new game session with the given host name.
    """
    session_id = session_manager.create_session(request.host_name, request.players, request.scores)
    return {"session_id": session_id, "Host": request.host_name, "message": "Game session created successfully."}

@router.get("/{game_code}", tags=["Game"])
def get_game(game_code: str):
    """
    Retrieve the game session details by game code.
    """
    session = session_manager.get_session(game_code)
    if not session:
        raise HTTPException(status_code=404, detail="Game session not found.")
    return session

@router.get("/", tags=["Game"])
def get_all_games():
    """
    Retrieve all games.
    """
    session = session_manager.get_all_sessions()
    return session
