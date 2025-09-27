from pydantic import BaseModel


class GameCreation(BaseModel):
    genre: str
    rules: str


class GameSessionCreation(BaseModel):
    host_name: str
    number_of_questions: int
    game_code: str
    owner_player_id: str  # Added to track session owner
    is_public: bool = True  # New field to indicate if the session is public


class GameJoinRequest(BaseModel):
    session_code: str
    player_id: str
