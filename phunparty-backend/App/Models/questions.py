from pydantic import BaseModel

class Question(BaseModel):
    question_id: str
    question: str
    answer: str
    genre: str