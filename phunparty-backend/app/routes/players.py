from datetime import UTC, datetime
from typing import List

from app.database.dbCRUD import (create_player, delete_player, get_all_players,
                                 get_all_sessions_from_player,
                                 get_game_history_for_player,
                                 get_player_by_email, get_player_by_ID,
                                 update_player)
from app.dependencies import get_current_player, get_db, require_admin_api_key
from app.models.players import Player, PlayerUpdate
from app.models.response_models import PlayerResponse
from app.schemas.players_model import Players
from app.schemas.session_player_assignment_model import SessionAssignment
from app.security.ownership import assert_same_player
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter()


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.post("/create", tags=["Players"])
async def create_player_route(
    request: Request,
    player: Player,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="register-ip",
        identifier=get_client_ip(request),
        limit=5,
        window_seconds=3600,
    )
    await enforce_rate_limit(
        request,
        scope="register-email",
        identifier=player.player_email,
        limit=3,
        window_seconds=3600,
    )

    try:
        existing_player = get_player_by_email(db, player.player_email)
        if existing_player:
            raise HTTPException(
                status_code=400, detail="Account with this email already exists"
            )
        return create_player(
            db,
            player.player_name,
            player.player_email,
            player.player_mobile,
            player.hashed_password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Account with this email or phone number already exists",
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create account")


@router.get("/me", response_model=PlayerResponse, tags=["Players"])
def get_me(current_player: Players = Depends(get_current_player)):
    return current_player


@router.get("/me/owned-sessions", tags=["Players"])
def get_my_owned_sessions(
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    return get_all_sessions_from_player(db, current_player.player_id)


@router.get("/{player_id}", response_model=PlayerResponse, tags=["Players"])
def get_player_route(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    assert_same_player(current_player, player_id)

    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        return player
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve player information"
        )


@router.get("/", response_model=List[PlayerResponse], tags=["Players"])
def get_all_players_route(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_api_key),
):
    try:
        players = get_all_players(db)
        if not players:
            raise HTTPException(status_code=404, detail="No players found")
        return players
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Unable to retrieve players list")


@router.delete("/{player_id}", tags=["Players"])
def delete_player_route(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    assert_same_player(current_player, player_id)

    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        return delete_player(db, player_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to deactivate account")


@router.put("/{player_id}", tags=["Players"])
def update_player_route(
    player_id: str,
    player: PlayerUpdate,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    assert_same_player(current_player, player_id)

    try:
        existing_player = get_player_by_ID(db, player_id)
        if not existing_player:
            raise HTTPException(status_code=404, detail="Player not found")
        updated_player = update_player(db, player_id, player)
        if not updated_player:
            raise HTTPException(status_code=400, detail="Failed to update player")
        return updated_player
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Account with this email or phone number already exists",
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Unable to update player information"
        )


@router.get("/allOwnedSessions/{player_id}", tags=["Players"])
def get_all_sessions_route(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    assert_same_player(current_player, player_id)

    try:
        return get_all_sessions_from_player(db, player_id)
    except Exception:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve player sessions"
        )


@router.get(
    "/allSessions/{player_id}", response_model=List[PlayerResponse], tags=["Players"]
)
def get_player_gameplay_history(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    assert_same_player(current_player, player_id)

    try:
        history = get_game_history_for_player(db, player_id)
        if not history:
            raise HTTPException(
                status_code=404, detail="No gameplay history found for this player"
            )
        return history
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve gameplay history"
        )


@router.post("/leave-session/{player_id}", tags=["Players"])
def leave_session_route(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    assert_same_player(current_player, player_id)

    try:
        player = get_player_by_ID(db, player_id)

        if player:
            player.active_game_code = None
            active_assignments = (
                db.query(SessionAssignment)
                .filter(SessionAssignment.player_id == player_id)
                .filter(SessionAssignment.session_end.is_(None))
                .all()
            )

            for assignment in active_assignments:
                assignment.session_end = utc_now()

            db.commit()

        return {"detail": "Player left the session successfully"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to leave session")


@router.get("/debug/player-status/{player_id}", tags=["Players"])
def get_player_status_route(
    player_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_api_key),
):
    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

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
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get player status")
