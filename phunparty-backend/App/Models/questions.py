from pydantic import BaseModel
from sqlalchemy import Enum

class Question(BaseModel):
    question: str
    answer: str
    genre: str
    difficulty: Enum