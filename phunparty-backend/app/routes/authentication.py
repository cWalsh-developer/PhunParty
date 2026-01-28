from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import (
    get_player_by_email,
    reactivate_player,
)
from app.dependencies import get_db
from app.models.loginRequest import LoginRequest
from app.utils.generateJWT import create_access_token
from app.utils.hash_password import verify_password

router = APIRouter()


@router.post("/login", tags=["Players"])
def login_route(login_request: LoginRequest, db: Session = Depends(get_db)):
    """
    Login to player account. Automatically reactivates deactivated accounts within grace period.
    """
    try:
        # Get player by email (includes deactivated, excludes deleted)
        player = get_player_by_email(db, login_request.player_email)

        if not player:
            raise HTTPException(
                status_code=404, detail="No account found with this email address"
            )

        # If account is deactivated, try to reactivate
        if player.is_deactivated:
            try:
                reactivate_player(db, player)
                # Account reactivated successfully, continue with login
            except ValueError as e:
                # Grace period expired or other error
                raise HTTPException(status_code=403, detail=str(e))

        # Verify password
        if not verify_password(login_request.password, player.hashed_password):
            raise HTTPException(status_code=401, detail="Incorrect password")

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
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Login service temporarily unavailable"
        )
