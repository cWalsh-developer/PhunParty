from app.database.dbCRUD import (
    create_player,
    get_player_by_ID,
    get_all_players,
    delete_player,
    update_player_name,
    update_player_score,
    update_player_game_code,
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


@router.put("/{player_id}/score", tags=["Players"])
def update_player_score_route(
    player_id: str, score: int, db: Session = Depends(get_db)
):
    """
    Update the score of a player.
    """
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    updated_player = update_player_score(db, player_id, score)
    return updated_player


@router.put("/{player_id}/name", tags=["Players"])
def update_player_name_route(
    player_id: str, player_name: str, db: Session = Depends(get_db)
):
    """
    Update the name of a player.
    """
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    updated_player = update_player_name(db, player_id, player_name)
    if not updated_player:
        raise HTTPException(status_code=400, detail="Failed to update player name")
    return updated_player


@router.put("/{player_id}/game_code", tags=["Players"])
def update_player_game_code_route(
    player_id: str, game_code: str, db: Session = Depends(get_db)
):
    """
    Update the game code of a player.
    """
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    updated_player = update_player_game_code(db, player_id, game_code)
    return updated_player
