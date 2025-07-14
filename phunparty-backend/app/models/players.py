from pydantic import BaseModel
from typing import Optional

class Player(BaseModel):
    game_code: Optional[str] = None
    player_name: str
    player_email: str
    player_mobile: Optional[str] = None