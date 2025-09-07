from pydantic import BaseModel


class LoginRequest(BaseModel):
    player_email: str
    password: str
