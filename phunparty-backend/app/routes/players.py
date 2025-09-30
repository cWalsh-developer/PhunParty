##from app.models.players_model import Players
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import (
    create_player,
    delete_player,
    get_all_players,
    get_all_sessions_from_player,
    get_game_history_for_player,
    get_player_by_email,
    get_player_by_ID,
    update_player,
    update_player_game_code,
)
from app.dependencies import get_api_key, get_db
from app.models.loginRequest import LoginRequest
from app.models.players import Player, PlayerUpdate
from app.models.response_models import PlayerResponse
from app.utils.generateJWT import create_access_token
from app.utils.hash_password import verify_password

router = APIRouter(dependencies=[Depends(get_api_key)])


@router.post("/create", tags=["Players"])
def create_player_route(player: Player, db: Session = Depends(get_db)):
    """
    Create a new player in the game.
    """
    existing_player = get_player_by_email(db, player.player_email)
    if existing_player:
        raise HTTPException(
            status_code=400, detail="Player with this email already exists"
        )
    try:
        game = create_player(
            db,
            player.player_name,
            player.player_email,
            player.player_mobile,
            player.hashed_password,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create player")
    return game


@router.get("/{player_id}", response_model=PlayerResponse, tags=["Players"])
def get_player_route(player_id: str, db: Session = Depends(get_db)):
    """
    Retrieve a player by their ID.
    """
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.get("/", response_model=List[PlayerResponse], tags=["Players"])
def get_all_players_route(db: Session = Depends(get_db)):
    """
    Retrieve all players in the game.
    """
    players = get_all_players(db)
    if not players:
        raise HTTPException(status_code=404, detail="No players found")
    return players


@router.delete("/{player_id}", tags=["Players"])
def delete_player_route(player_id: str, db: Session = Depends(get_db)):
    """
    Delete a player from the game.
    """
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    delete_player(db, player_id)
    return {"detail": "Player deleted successfully"}


@router.put("/{player_id}", tags=["Players"])
def update_player_route(
    player_id: str, player: PlayerUpdate, db: Session = Depends(get_db)
):
    """
    Update the name of a player.
    """
    existing_player = get_player_by_ID(db, player_id)
    if not existing_player:
        raise HTTPException(status_code=404, detail="Player not found")
    updated_player = update_player(db, player_id, player)
    if not updated_player:
        raise HTTPException(status_code=400, detail="Failed to update player")
    return updated_player


@router.get(
    "/allOwnedSessions/{player_id}",
    tags=["Players"],
)
def get_all_sessions_route(player_id: str, db: Session = Depends(get_db)):
    """
    Get all active game sessions for a specific player.
    """
    sessions = get_all_sessions_from_player(db, player_id)
    if not sessions:
        raise HTTPException(
            status_code=404, detail="No active sessions found for this player"
        )
    return sessions


@router.get(
    "/allSessions/{player_id}", response_model=List[PlayerResponse], tags=["Players"]
)
def get_player_gameplay_history(player_id: str, db: Session = Depends(get_db)):
    """
    Get the gameplay history for a specific player.
    """
    history = get_game_history_for_player(db, player_id)
    if not history:
        raise HTTPException(
            status_code=404, detail="No gameplay history found for this player"
        )
    return history


@router.post("/leave-session", tags=["Players"])
def leave_session_route(player_id: str, db: Session = Depends(get_db)):
    """
    Leave a game session.
    """
    try:
        update_player_game_code(db, player_id, None)
        return {"detail": "Player left the session successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to leave session")
