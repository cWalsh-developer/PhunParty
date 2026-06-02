import secrets


FRIEND_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
FRIEND_CODE_LENGTH = 6


def generate_friend_code(length: int = FRIEND_CODE_LENGTH) -> str:
    return "".join(secrets.choice(FRIEND_CODE_ALPHABET) for _ in range(length))


def normalize_friend_code(friend_code: str) -> str:
    return (friend_code or "").replace("PHUN-", "").replace(" ", "").upper().strip()
