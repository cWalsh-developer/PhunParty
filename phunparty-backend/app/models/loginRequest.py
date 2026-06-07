from pydantic import BaseModel


class LoginRequest(BaseModel):
    player_email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
