from pydantic import BaseModel
from app.models.enums import DifficultyLevel


class Question(BaseModel):
    question: str
    answer: str
    genre: str
    difficulty: DifficultyLevel
    question_options: list[str]


class AnswerVerification(BaseModel):
    question_id: str
    session_code: str
    player_id: str
    player_answer: str
    is_correct: bool
