import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.api_key import APIKeyHeader
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import SessionLocal
from app.schemas.players_model import Players
from app.security.rls import clear_rls_context, set_rls_current_player
from app.utils.generateJWT import ALGORITHM, SECRET_KEY

load_dotenv("credentials.env")

API_KEY = os.getenv("API_KEY")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
API_KEY_NAME = "X-API-Key"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)
bearer_scheme = HTTPBearer(auto_error=True)


def get_api_key(api_key: str = Security(api_key_header)):
    if API_KEY and secrets.compare_digest(api_key, API_KEY):
        return api_key
    raise HTTPException(status_code=403, detail="Could not validate API key")


def require_admin_api_key(api_key: str = Security(api_key_header)):
    if ADMIN_API_KEY and secrets.compare_digest(api_key, ADMIN_API_KEY):
        return api_key
    raise HTTPException(status_code=403, detail="Admin access required")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        clear_rls_context(db)
        db.close()


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")


def get_player_from_token_value(token: str, db: Session) -> Players:
    payload = decode_access_token(token)
    player_id = payload.get("sub")
    if not player_id:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    set_rls_current_player(db, player_id)

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


def get_current_player(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: Session = Depends(get_db),
) -> Players:
    return get_player_from_token_value(credentials.credentials, db)
