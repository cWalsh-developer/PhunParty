from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_player_by_email
from app.dependencies import get_db
from app.models.loginRequest import LoginRequest
from app.utils.generateJWT import create_access_token
from app.utils.hash_password import verify_password

router = APIRouter()


@router.post("/login", tags=["Players"])
def login_route(login_request: LoginRequest, db: Session = Depends(get_db)):
    """
    Retrieve a player by their email.
    """
    try:
        player = get_player_by_email(db, login_request.player_email)
        if not player:
            raise HTTPException(
                status_code=404, detail="No account found with this email address"
            )
        if not verify_password(login_request.password, player.hashed_password):
            raise HTTPException(status_code=401, detail="Incorrect password")
        else:
            access_token = create_access_token(
                data={
                    "sub": player.player_id,
                }
            )
            return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Login service temporarily unavailable"
        )
