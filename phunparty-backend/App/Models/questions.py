from pydantic import BaseModel

class Question(BaseModel):
    question: str
    answer: str
    genre: str