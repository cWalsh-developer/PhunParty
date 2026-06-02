import os

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import SessionLocal
from app.schemas.players_model import Players
from app.utils.generateJWT import ALGORITHM, SECRET_KEY

load_dotenv("credentials.env")

API_KEY = os.getenv("API_KEY")
API_KEY_NAME = "X-API-Key"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)
bearer_scheme = HTTPBearer(auto_error=True)


def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == API_KEY:
        return api_key
    else:
        raise HTTPException(status_code=403, detail="Could not validate API key")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_player(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: Session = Depends(get_db),
) -> Players:
    try:
        payload = jwt.decode(
            credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM]
        )
        player_id = payload.get("sub")
        if not player_id:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    player = (
        db.query(Players)
        .filter(Players.player_id == player_id)
        .filter(Players.is_deleted == False)
        .filter(Players.is_deactivated == False)
        .first()
    )
    if not player:
        raise HTTPException(status_code=401, detail="Player account is not available")
    return player
