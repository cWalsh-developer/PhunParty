import hashlib
import os


def _safe_hashed_ref(prefix: str, value: str | None) -> str:
    if not value:
        return "unknown"

    salt = os.getenv("LOG_HASH_SALT", "dev-only-change-me")
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()

    return f"{prefix}_{digest[:10]}"


def safe_player_ref(player_id: str | None) -> str:
    return _safe_hashed_ref("player", player_id)


def safe_session_ref(session_code: str | None) -> str:
    return _safe_hashed_ref("session", session_code)
