from app.models.enums import DifficultyLevel
from app.security.input_validation import SanitizedRequestModel
from pydantic import ConfigDict, StrictStr, field_validator


class Question(SanitizedRequestModel):
    model_config = ConfigDict(extra="ignore", strict=False, str_strip_whitespace=True)

    question: StrictStr
    answer: StrictStr
    genre: StrictStr
    difficulty: DifficultyLevel
    question_options: list[StrictStr]

    @field_validator("difficulty", mode="before")
    @classmethod
    def validate_difficulty(cls, value):
        if isinstance(value, DifficultyLevel):
            return value
        if isinstance(value, str):
            return DifficultyLevel(value.lower())
        raise ValueError("difficulty must be easy, medium, or hard")


class AnswerVerification(SanitizedRequestModel):
    question_id: str
    session_code: str
    player_id: str
    player_answer: str
    is_correct: bool
