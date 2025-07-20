from app.database.dbCRUD import (
    create_player,
    get_player_by_ID,
    get_all_players,
    delete_player,
    update_player,
)
from app.dependencies import get_db

##from app.models.players_model import Players
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from app.models.players import Player
from app.models.response_models import PlayerResponse
from sqlalchemy.orm import Session

router = APIRouter()


@router.post("/create", tags=["Players"])
def create_player_route(player: Player, db: Session = Depends(get_db)):
    """
    Create a new player in the game.
    """
    game = create_player(
        db, player.player_name, player.player_email, player.player_mobile
    )
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
def update_player_route(player_id: str, player: Player, db: Session = Depends(get_db)):
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
