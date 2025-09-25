from typing import Optional

from pydantic import BaseModel


class Player(BaseModel):
    game_code: Optional[str] = None
    player_name: str
    player_email: str
    hashed_password: str
    player_mobile: Optional[str] = None
    profile_photo_url: Optional[str] = None


class PlayerUpdate(BaseModel):
    game_code: Optional[str] = None
    player_name: Optional[str] = None
    player_email: Optional[str] = None
    hashed_password: Optional[str] = None  # Now optional
    player_mobile: Optional[str] = None
    profile_photo_url: Optional[str] = None
