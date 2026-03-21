from sqlalchemy import JSON, Column, String, Enum as SAEnum
from app.config import Base
from app.models.enums import DifficultyLevel


class Questions(Base):
    __tablename__ = "questions"
    question_id = Column(String, primary_key=True, index=False)
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)
    genre = Column(String, nullable=False)
    difficulty = Column(
        SAEnum(DifficultyLevel, name="difficulty_levels"),
        default=DifficultyLevel.easy,
        nullable=False,
    )
    question_options = Column(JSON, nullable=False)
