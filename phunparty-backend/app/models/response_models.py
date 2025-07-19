from pydantic import BaseModel
from typing import Optional
from app.models.enums import ResultType, DifficultyLevel


class GameResponse(BaseModel):
    game_code: str
    rules: str
    genre: str


class PlayerResponse(BaseModel):
    player_id: str
    player_name: str
    player_email: str
    player_mobile: str = None
    active_game_code: Optional[str] = None


class AnswerVerificationResponseModel(BaseModel):
    player_answer: str
    is_correct: bool


class ScoresResponseModel(BaseModel):
    score: int
    result: Optional[ResultType] = None


class QuestionRequest(BaseModel):
    question_id: str
    question: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_answer: str
    genre: str


class QuestionsAddedResponseModel(BaseModel):
    message: str
    question_id: str
    question: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_answer: str
    genre: str

    class Config:
        from_attributes = True
