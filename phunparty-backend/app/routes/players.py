import logging
from datetime import UTC, datetime
from typing import List

from app.database.dbCRUD import (
    create_player,
    delete_player,
    get_all_players,
    get_all_sessions_from_player,
    get_game_history_for_player,
    get_player_by_email,
    get_player_by_ID,
    issue_email_verification_code,
    update_player,
    verify_player_email_code,
)
from app.dependencies import get_current_player, get_db, require_admin_api_key
from app.models.players import (
    EmailVerificationRequest,
    EmailVerificationResendRequest,
    Player,
    PlayerUpdate,
)
from app.models.response_models import PlayerResponse
from app.schemas.players_model import Players
from app.schemas.session_player_assignment_model import SessionAssignment
from app.security.cache import invalidate_profile_cache, invalidate_social_cache
from app.security.ownership import assert_same_player
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from app.utils.email_verification import send_email_verification_code
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter()
logger = logging.getLogger(__name__)


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
        new_player = create_player(
            db,
            player.player_name,
            player.player_email,
            player.player_mobile,
            player.hashed_password,
        )
        verification_code = issue_email_verification_code(db, new_player)
        try:
            send_email_verification_code(new_player.player_email, verification_code)
        except Exception:
            logger.exception(
                "Failed to send email verification code to %s",
                new_player.player_email,
            )
        return new_player
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


@router.post("/verify-email", tags=["Players"])
async def verify_email_route(
    request: Request,
    payload: EmailVerificationRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="email-verify-ip",
        identifier=get_client_ip(request),
        limit=12,
        window_seconds=900,
    )
    await enforce_rate_limit(
        request,
        scope="email-verify-email",
        identifier=payload.player_email,
        limit=8,
        window_seconds=900,
    )

    if not verify_player_email_code(db, payload.player_email, payload.code):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired verification code",
        )

    return {"message": "Email verified"}


@router.post("/resend-verification", tags=["Players"])
async def resend_email_verification_route(
    request: Request,
    payload: EmailVerificationResendRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="email-resend-ip",
        identifier=get_client_ip(request),
        limit=5,
        window_seconds=900,
    )
    await enforce_rate_limit(
        request,
        scope="email-resend-email",
        identifier=payload.player_email,
        limit=3,
        window_seconds=900,
    )

    player = get_player_by_email(db, payload.player_email)
    if not player:
        return {"message": "If that account exists, a verification code was sent"}

    if player.email_verified:
        return {"message": "Email already verified"}

    verification_code = issue_email_verification_code(db, player)
    try:
        send_email_verification_code(player.player_email, verification_code)
    except Exception:
        logger.exception(
            "Failed to resend email verification code to %s",
            player.player_email,
        )

    return {"message": "Verification code sent"}


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
        result = delete_player(db, player_id)
        invalidate_profile_cache(player_id)
        invalidate_social_cache(player_id)
        return result
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
        invalidate_profile_cache(player_id)
        invalidate_social_cache(player_id)
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
