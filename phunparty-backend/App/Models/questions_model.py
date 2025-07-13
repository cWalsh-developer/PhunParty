from sqlalchemy import Column, String, Enum
from app.config import Base

class Questions(Base):
    status_enum = Enum("easy", "medium", "hard", name="difficulty_enum")
    __tablename__ = "questions"
    question_id = Column(String, primary_key=True, index=False)
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)
    genre = Column(String, nullable=False)
    difficulty = Column(status_enum, default= "easy", nullable=False)