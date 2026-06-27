from app.database.dbCRUD import get_player_by_email, reactivate_player
from app.database.refresh_token_crud import (
    create_refresh_session,
    get_refresh_session_by_token,
    revoke_all_player_refresh_tokens,
    revoke_refresh_session,
    rotate_refresh_session,
)
from app.dependencies import get_current_player, get_db
from app.models.loginRequest import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
)
from app.schemas.players_model import Players
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from app.security.rls import set_rls_current_player, set_rls_login_email
from app.utils.generateJWT import ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token
from app.utils.hash_password import hash_password, verify_password
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

router = APIRouter()
INVALID_LOGIN_MESSAGE = "Invalid email or password"


def token_response(access_token: str, refresh_token: str | None = None) -> dict:
    response = {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }
    if refresh_token is not None:
        response["refresh_token"] = refresh_token
    return response


@router.post("/login", tags=["Players"])
async def login_route(
    request: Request,
    login_request: LoginRequest,
    db: Session = Depends(get_db),
):
    """
    Login to player account. Automatically reactivates deactivated accounts within grace period.
    """
    await enforce_rate_limit(
        request,
        scope="login-ip",
        identifier=get_client_ip(request),
        limit=20,
        window_seconds=900,
    )
    await enforce_rate_limit(
        request,
        scope="login-email",
        identifier=login_request.player_email,
        limit=10,
        window_seconds=900,
    )

    try:
        set_rls_login_email(db, login_request.player_email)

        # Get player by email (includes deactivated, excludes deleted)
        player = get_player_by_email(db, login_request.player_email)

        if not player:
            raise HTTPException(status_code=401, detail=INVALID_LOGIN_MESSAGE)
        else:
            # Verify password
            if not verify_password(login_request.password, player.hashed_password):
                raise HTTPException(status_code=401, detail=INVALID_LOGIN_MESSAGE)
            else:
                set_rls_current_player(db, player.player_id)

                # If account is deactivated, try to reactivate
                if player.is_deactivated:
                    try:
                        reactivate_player(db, player)
                        # Account reactivated successfully, continue with login
                    except ValueError as e:
                        # Grace period expired or other error
                        raise HTTPException(status_code=403, detail=str(e))
                # Generate access token
                access_token = create_access_token(
                    data={
                        "sub": player.player_id,
                    }
                )
                refresh_token, _refresh_record = create_refresh_session(
                    db,
                    player.player_id,
                    user_agent=request.headers.get("user-agent"),
                    ip_address=get_client_ip(request),
                    device_id=login_request.device_id,
                )
                return {
                    **token_response(access_token, refresh_token),
                    "player_id": player.player_id,
                    "player_name": player.player_name,
                    "user": {
                        "player_id": player.player_id,
                        "player_name": player.player_name,
                        "player_email": player.player_email,
                        "player_mobile": player.player_mobile,
                        "active_game_code": player.active_game_code,
                    },
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Login service temporarily unavailable"
        )


@router.post("/refresh", tags=["Players"])
async def refresh_access_token(
    request: Request,
    refresh_request: RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="refresh-token-ip",
        identifier=get_client_ip(request),
        limit=60,
        window_seconds=3600,
    )
    await enforce_rate_limit(
        request,
        scope="refresh-token-value",
        identifier=refresh_request.refresh_token,
        limit=20,
        window_seconds=3600,
    )

    existing, match_type = get_refresh_session_by_token(
        db,
        refresh_request.refresh_token,
        lock=True,
    )
    if not existing:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if match_type == "reused_previous":
        revoke_refresh_session(
            db,
            existing,
            reason="previous_refresh_token_reused",
        )
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    set_rls_current_player(db, existing.player_id)
    player = (
        db.query(Players)
        .filter(Players.player_id == existing.player_id)
        .filter(Players.is_deleted == False)
        .filter(Players.is_deactivated == False)
        .first()
    )
    if not player:
        revoke_refresh_session(
            db,
            existing,
            reason="player_account_unavailable",
        )
        raise HTTPException(status_code=401, detail="Player account is not available")

    try:
        new_refresh_token, _session = rotate_refresh_session(
            db,
            existing,
            user_agent=request.headers.get("user-agent"),
            ip_address=get_client_ip(request),
            device_id=refresh_request.device_id,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to refresh token")

    access_token = create_access_token(data={"sub": player.player_id})
    return token_response(access_token, new_refresh_token)


@router.post("/logout", tags=["Players"])
async def logout(
    request: Request,
    logout_request: LogoutRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="logout-ip",
        identifier=get_client_ip(request),
        limit=60,
        window_seconds=3600,
    )
    existing, _match_type = get_refresh_session_by_token(
        db, logout_request.refresh_token
    )
    if existing:
        revoke_refresh_session(db, existing, reason="logout")

    return {"detail": "Logged out"}


@router.put("/change-password", tags=["Players"])
async def change_password_route(
    request: Request,
    change_request: ChangePasswordRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="change-password-ip",
        identifier=get_client_ip(request),
        limit=10,
        window_seconds=3600,
    )
    await enforce_rate_limit(
        request,
        scope="change-password-player",
        identifier=current_player.player_id,
        limit=5,
        window_seconds=3600,
    )

    if not verify_password(
        change_request.current_password,
        current_player.hashed_password,
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    current_player.hashed_password = hash_password(change_request.new_password)
    db.commit()
    revoke_all_player_refresh_tokens(db, current_player.player_id)

    access_token = create_access_token(data={"sub": current_player.player_id})
    refresh_token, _refresh_record = create_refresh_session(
        db,
        current_player.player_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=get_client_ip(request),
    )
    return {
        "message": "Password changed successfully",
        **token_response(access_token, refresh_token),
    }
