from app.database.dbCRUD import get_player_by_email, reactivate_player
from app.dependencies import get_current_player, get_db
from app.models.loginRequest import ChangePasswordRequest, LoginRequest
from app.schemas.players_model import Players
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from app.security.rls import set_rls_current_player, set_rls_login_email
from app.utils.generateJWT import create_access_token
from app.utils.hash_password import hash_password, verify_password
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

router = APIRouter()
INVALID_LOGIN_MESSAGE = "Invalid email or password"


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
                return {
                    "access_token": access_token,
                    "token_type": "bearer",
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

    access_token = create_access_token(data={"sub": current_player.player_id})
    return {
        "message": "Password changed successfully",
        "access_token": access_token,
        "token_type": "bearer",
    }
