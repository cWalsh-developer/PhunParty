from app.database.dbCRUD import (
    get_player_by_email,
)
from app.dependencies import get_db
from app.utils.hash_password import verify_password
from app.utils.generateJWT import create_access_token
from fastapi import APIRouter, HTTPException, Depends
from app.models.loginRequest import LoginRequest
from sqlalchemy.orm import Session

router = APIRouter()


@router.post("/login", tags=["Players"])
def login_route(login_request: LoginRequest, db: Session = Depends(get_db)):
    """
    Retrieve a player by their email.
    """
    player = get_player_by_email(db, login_request.player_email)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    if not verify_password(login_request.password, player.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid password")
    else:
        access_token = create_access_token(
            data={
                "sub": player.player_id,
                "email": player.player_email,
                "name": player.player_name,
                "mobile": player.player_mobile,
            }
        )
        return {"access_token": access_token, "token_type": "bearer"}
