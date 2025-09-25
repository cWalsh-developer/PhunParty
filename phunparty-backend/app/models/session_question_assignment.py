from sqlalchemy import Column, DateTime, ForeignKey, String

from app.config import Base


class SessionQuestionAssignment(Base):
    __tablename__ = "session_question_assignments"
    assignment_id = Column(String, primary_key=True, index=False)
    question_id = Column(String, ForeignKey("questions.question_id"), nullable=False)
    session_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=False
    )
