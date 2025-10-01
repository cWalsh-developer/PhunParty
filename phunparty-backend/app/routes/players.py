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
    try:
        existing_player = get_player_by_email(db, player.player_email)
        if existing_player:
            raise HTTPException(
                status_code=400, detail="Account with this email already exists"
            )
        game = create_player(
            db,
            player.player_name,
            player.player_email,
            player.player_mobile,
            player.hashed_password,
        )
        return game
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create account")


@router.get("/{player_id}", response_model=PlayerResponse, tags=["Players"])
def get_player_route(player_id: str, db: Session = Depends(get_db)):
    """
    Retrieve a player by their ID.
    """
    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        return player
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve player information"
        )


@router.get("/", response_model=List[PlayerResponse], tags=["Players"])
def get_all_players_route(db: Session = Depends(get_db)):
    """
    Retrieve all players in the game.
    """
    try:
        players = get_all_players(db)
        if not players:
            raise HTTPException(status_code=404, detail="No players found")
        return players
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Unable to retrieve players list")


@router.delete("/{player_id}", tags=["Players"])
def delete_player_route(player_id: str, db: Session = Depends(get_db)):
    """
    Delete a player from the game.
    """
    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        delete_player(db, player_id)
        return {"detail": "Player deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete player")


@router.put("/{player_id}", tags=["Players"])
def update_player_route(
    player_id: str, player: PlayerUpdate, db: Session = Depends(get_db)
):
    """
    Update the name of a player.
    """
    try:
        existing_player = get_player_by_ID(db, player_id)
        if not existing_player:
            raise HTTPException(status_code=404, detail="Player not found")
        updated_player = update_player(db, player_id, player)
        if not updated_player:
            raise HTTPException(status_code=400, detail="Failed to update player")
        return updated_player
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to update player information"
        )


@router.get(
    "/allOwnedSessions/{player_id}",
    tags=["Players"],
)
def get_all_sessions_route(player_id: str, db: Session = Depends(get_db)):
    """
    Get all active game sessions for a specific player.
    """
    try:
        sessions = get_all_sessions_from_player(db, player_id)
        if not sessions:
            raise HTTPException(
                status_code=404, detail="No active sessions found for this player"
            )
        return sessions
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve player sessions"
        )


@router.get(
    "/allSessions/{player_id}", response_model=List[PlayerResponse], tags=["Players"]
)
def get_player_gameplay_history(player_id: str, db: Session = Depends(get_db)):
    """
    Get the gameplay history for a specific player.
    """
    try:
        history = get_game_history_for_player(db, player_id)
        if not history:
            raise HTTPException(
                status_code=404, detail="No gameplay history found for this player"
            )
        return history
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve gameplay history"
        )


@router.post("/leave-session/{player_id}", tags=["Players"])
def leave_session_route(player_id: str, db: Session = Depends(get_db)):
    """
    Leave a game session.
    """
    try:
        # Check if player exists first
        player = get_player_by_ID(db, player_id)

        if player:
            # Update player's active game code to None
            player.active_game_code = None

            # Also clear any session assignments for this player
            from app.models.session_player_assignment_model import SessionAssignment
            from datetime import datetime

            # End any active session assignments (those without session_end)
            active_assignments = (
                db.query(SessionAssignment)
                .filter(SessionAssignment.player_id == player_id)
                .filter(SessionAssignment.session_end.is_(None))
                .all()
            )

            for assignment in active_assignments:
                assignment.session_end = datetime.utcnow()

            db.commit()

        # Always return success - if player doesn't exist, they're not in a session anyway
        return {"detail": "Player left the session successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to leave session: {str(e)}"
        )


@router.get("/debug/player-status/{player_id}", tags=["Players"])
def get_player_status_route(player_id: str, db: Session = Depends(get_db)):
    """
    Debug endpoint: Get detailed player status.
    """
    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        # Get active assignments
        from app.models.session_player_assignment_model import SessionAssignment

        active_assignments = (
            db.query(SessionAssignment)
            .filter(SessionAssignment.player_id == player_id)
            .filter(SessionAssignment.session_end.is_(None))
            .all()
        )

        return {
            "player_id": player.player_id,
            "player_name": player.player_name,
            "active_game_code": player.active_game_code,
            "active_assignments": [
                {
                    "session_code": assignment.session_code,
                    "session_start": assignment.session_start,
                    "assignment_id": assignment.assignment_id,
                }
                for assignment in active_assignments
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get player status: {str(e)}"
        )
