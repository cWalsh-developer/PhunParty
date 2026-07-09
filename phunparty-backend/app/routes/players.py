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
    issue_email_verification_token,
    update_player,
    verify_player_email_code,
    verify_player_email_token,
)
from app.database.email_verification_migrations import (
    ensure_email_verification_columns,
)
from app.database.refresh_token_crud import create_refresh_session
from app.dependencies import get_current_player, get_db, require_admin_api_key
from app.models.players import (
    EmailVerificationRequest,
    EmailVerificationResendRequest,
    EmailVerificationTokenRequest,
    Player,
    PlayerUpdate,
)
from app.models.response_models import PlayerResponse
from app.schemas.players_model import Players
from app.schemas.session_player_assignment_model import SessionAssignment
from app.security.cache import invalidate_profile_cache, invalidate_social_cache
from app.security.ownership import assert_same_player
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from app.security.rls import set_rls_login_email
from app.utils.email_verification import (
    email_verification_expires_at,
    generate_email_verification_token,
    hash_email_verification_token,
    send_email_verification_link,
)
from app.utils.generateJWT import ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter()
logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def verification_login_response(
    player: Players,
    request: Request,
    db: Session,
) -> dict:
    access_token = create_access_token(data={"sub": player.player_id})
    refresh_token, _refresh_record = create_refresh_session(
        db,
        player.player_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=get_client_ip(request),
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "player_id": player.player_id,
        "player_name": player.player_name,
        "user": {
            "player_id": player.player_id,
            "player_name": player.player_name,
            "player_email": player.player_email,
            "player_mobile": player.player_mobile,
            "active_game_code": player.active_game_code,
            "email_verified": player.email_verified,
        },
    }


def send_player_email_verification_link(
    player_email: str, verification_token: str
) -> bool:
    try:
        return send_email_verification_link(player_email, verification_token)
    except Exception:
        logger.exception(
            "Failed to send email verification link to %s",
            player_email,
        )
        return False


async def enforce_email_verification_send_limits(
    request: Request, player_email: str
) -> None:
    await enforce_rate_limit(
        request,
        scope="email-verification-send-ip",
        identifier=get_client_ip(request),
        limit=10,
        window_seconds=3600,
    )
    await enforce_rate_limit(
        request,
        scope="email-verification-send-email",
        identifier=player_email,
        limit=3,
        window_seconds=3600,
    )


@router.post("/create", tags=["Players"])
async def create_player_route(
    request: Request,
    background_tasks: BackgroundTasks,
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
        ensure_email_verification_columns()
        set_rls_login_email(db, player.player_email)
        existing_player = get_player_by_email(db, player.player_email)
        if existing_player:
            if not existing_player.email_verified:
                await enforce_email_verification_send_limits(
                    request, player.player_email
                )
                verification_token = issue_email_verification_token(db, existing_player)
                background_tasks.add_task(
                    send_player_email_verification_link,
                    existing_player.player_email,
                    verification_token,
                )
                return {
                    "message": "If the account can be registered, a verification link will be sent."
                }
            raise HTTPException(
                status_code=400, detail="Account with this email already exists"
            )
        await enforce_email_verification_send_limits(request, player.player_email)
        verification_token = generate_email_verification_token()
        new_player = create_player(
            db,
            player.player_name,
            player.player_email,
            player.player_mobile,
            player.hashed_password,
            commit=False,
            email_verified=False,
            email_verification_code_hash=hash_email_verification_token(
                verification_token
            ),
            email_verification_expires_at=email_verification_expires_at(),
        )
        db.commit()
        db.refresh(new_player)
        background_tasks.add_task(
            send_player_email_verification_link,
            new_player.player_email,
            verification_token,
        )
        return PlayerResponse.model_validate(new_player).model_dump()
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
        db.rollback()
        logger.exception("Failed to create account for %s", player.player_email)
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


@router.post("/verify-email-token", tags=["Players"])
async def verify_email_token_route(
    request: Request,
    payload: EmailVerificationTokenRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="email-token-verify-ip",
        identifier=get_client_ip(request),
        limit=10,
        window_seconds=900,
    )
    await enforce_rate_limit(
        request,
        scope="email-token-verify-token",
        identifier=payload.token,
        limit=6,
        window_seconds=900,
    )

    player = verify_player_email_token(db, payload.token)
    if not player:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired verification link",
        )

    return verification_login_response(player, request, db)


@router.post("/resend-verification", tags=["Players"])
async def resend_email_verification_route(
    request: Request,
    background_tasks: BackgroundTasks,
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
    await enforce_email_verification_send_limits(request, payload.player_email)

    ensure_email_verification_columns()
    player = get_player_by_email(db, payload.player_email)
    if not player:
        return {"message": "If that account exists, a verification code was sent"}

    if player.email_verified:
        return {"message": "Email already verified"}

    verification_token = issue_email_verification_token(db, player)
    background_tasks.add_task(
        send_player_email_verification_link,
        player.player_email,
        verification_token,
    )

    return {"message": "Verification link sent"}


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
        requested_updates = player.model_dump(exclude_unset=True)
        protected_fields = {
            "hashed_password",
            "player_email",
            "player_mobile",
            "profile_photo_url",
        }
        attempted_protected_updates = [
            field
            for field in protected_fields
            if field in requested_updates and requested_updates[field] is not None
        ]
        if attempted_protected_updates:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Use the dedicated verification flow to update email, phone, "
                    "password, or profile photo."
                ),
            )
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
