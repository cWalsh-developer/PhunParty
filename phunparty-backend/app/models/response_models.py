from typing import Optional, List

from pydantic import BaseModel

from app.models.enums import DifficultyLevel, ResultType


class GameResponse(BaseModel):
    game_code: str
    rules: str
    genre: str


class PlayerResponse(BaseModel):
    player_id: str
    player_name: str
    player_email: str
    player_mobile: str = None
    profile_photo_url: Optional[str] = None
    active_game_code: Optional[str] = None


class AnswerVerificationResponseModel(BaseModel):
    player_answer: str
    is_correct: bool


class ScoresResponseModel(BaseModel):
    player_id: str
    score: int
    result: Optional[ResultType] = None
    session_code: str


class QuestionRequest(BaseModel):
    difficulty: DifficultyLevel
    question: str
    answer: str
    genre: str
    question_options: Optional[List[str]] = []


class QuestionsAddedResponseModel(BaseModel):
    message: str
    question: str
    answer: str
    genre: str
    difficulty: DifficultyLevel

    class Config:
        from_attributes = True


class SubmitAnswerRequest(BaseModel):
    session_code: str
    player_id: str
    question_id: str
    player_answer: str


class GameStatusResponse(BaseModel):
    session_code: str
    is_active: bool
    is_waiting_for_players: bool
    isstarted: bool
    current_question_index: int
    total_questions: int
    current_question: dict
    players: dict
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class GameHistoryResponse(BaseModel):
    session_code: str
    game_type: str
    did_win: bool

    class Config:
        from_attributes = True
