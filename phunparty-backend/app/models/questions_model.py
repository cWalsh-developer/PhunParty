from sqlalchemy import Column, String, Enum
from app.config import Base
from app.models.enums import DifficultyLevel


class Questions(Base):
    __tablename__ = "questions"
    question_id = Column(String, primary_key=True, index=False)
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)
    genre = Column(String, nullable=False)
    difficulty = Column(Enum(DifficultyLevel), default="easy", nullable=False)
