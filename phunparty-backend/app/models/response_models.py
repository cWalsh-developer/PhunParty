from typing import List, Optional

from app.models.enums import DifficultyLevel, HistoryResultType, ResultType
from app.security.input_validation import SanitizedRequestModel
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class GameResponse(BaseModel):
    game_code: str
    rules: str
    genre: str


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: str
    player_name: str
    player_email: str
    player_mobile: Optional[str] = None
    profile_photo_url: Optional[str] = None
    active_game_code: Optional[str] = None
    email_verified: bool = False


class AnswerVerificationResponseModel(BaseModel):
    player_answer: str
    is_correct: bool


class ScoresResponseModel(BaseModel):
    display_name: str
    player_photo_url: Optional[str] = None
    score: int
    result: Optional[ResultType] = None
    session_code: str


class QuestionRequest(SanitizedRequestModel):
    model_config = ConfigDict(extra="ignore", strict=False, str_strip_whitespace=True)

    difficulty: DifficultyLevel
    question: StrictStr
    answer: StrictStr
    genre: StrictStr
    question_options: Optional[List[StrictStr]] = Field(default_factory=list)

    @field_validator("difficulty", mode="before")
    @classmethod
    def validate_difficulty(cls, value):
        if isinstance(value, DifficultyLevel):
            return value
        if isinstance(value, str):
            return DifficultyLevel(value.lower())
        raise ValueError("difficulty must be easy, medium, or hard")


class QuestionsAddedResponseModel(BaseModel):
    message: str
    question: str
    answer: str
    genre: str
    difficulty: DifficultyLevel

    class Config:
        from_attributes = True


class SubmitAnswerRequest(SanitizedRequestModel):
    session_code: str
    question_id: str
    player_answer: str


class GameStatusResponse(BaseModel):
    session_code: str
    game_type: Optional[str] = None
    game_code: Optional[str] = None
    genre: Optional[str] = None
    is_active: bool
    is_waiting_for_players: bool
    isstarted: bool
    current_question_index: int
    total_questions: int
    current_question: Optional[dict] = None
    players: dict
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class GameHistoryResponse(BaseModel):
    session_code: str
    game_type: str
    did_win: HistoryResultType

    class Config:
        from_attributes = True
