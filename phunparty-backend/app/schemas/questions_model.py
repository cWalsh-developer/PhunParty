from app.config import Base, engine
from app.models.enums import DifficultyLevel
from sqlalchemy import JSON, Column
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, inspect


def _questions_table_has_column(column_name: str) -> bool:
    """Keep the ORM compatible with deployments missing newer question columns."""
    try:
        inspector = inspect(engine)
        if not inspector.has_table("questions"):
            return True
        return any(
            column["name"] == column_name
            for column in inspector.get_columns("questions")
        )
    except Exception:
        return True


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
    if _questions_table_has_column("question_options"):
        question_options = Column(JSON, nullable=False)
