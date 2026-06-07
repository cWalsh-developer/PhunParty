import hashlib
import hmac
import os


def make_roster_player_id(session_code: str | None, player_id: str | None) -> str:
    """
    Build a public, session-scoped player reference for roster payloads.

    The same player receives a different roster id in each session, so browser
    clients can key UI state without learning or correlating database player ids.
    """
    if not session_code or not player_id:
        return "unknown"

    secret = os.getenv("ROSTER_ID_SECRET") or os.getenv("LOG_HASH_SALT")
    if not secret:
        secret = "dev-only-change-me"

    message = f"{session_code}:{player_id}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"roster_{digest[:16]}"
