import hashlib
import os


def safe_player_ref(player_id: str | None) -> str:
    if not player_id:
        return "unknown"

    salt = os.getenv("LOG_HASH_SALT", "dev-only-change-me")
    digest = hashlib.sha256(f"{salt}:{player_id}".encode("utf-8")).hexdigest()

    return f"player_{digest[:10]}"
