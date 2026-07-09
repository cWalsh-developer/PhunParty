import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from jose import jwt

env_path = Path(__file__).resolve().parents[2] / "credentials.env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_AUDIENCE = "phunparty-api"
PASSWORD_RESET_AUDIENCE = "phunparty-password-reset"
ACCESS_TOKEN_TYPE = "access"
PASSWORD_RESET_TOKEN_TYPE = "password_reset"


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Default: 15 minutes. Use refresh-token rotation for longer lived sessions.
ACCESS_TOKEN_EXPIRE_MINUTES = int_env("ACCESS_TOKEN_EXPIRE_MINUTES", 15)


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()

    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    to_encode.update(
        {
            "aud": ACCESS_TOKEN_AUDIENCE,
            "exp": expire,
            "token_type": ACCESS_TOKEN_TYPE,
        }
    )
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_password_reset_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=10))

    to_encode.update(
        {
            "aud": PASSWORD_RESET_AUDIENCE,
            "exp": expire,
            "token_type": PASSWORD_RESET_TOKEN_TYPE,
        }
    )
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
