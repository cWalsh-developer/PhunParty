from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

from app.config import Base


class PlayerResponse(Base):
    """Track individual player responses to questions in a session"""

    __tablename__ = "player_responses"

    response_id = Column(String, primary_key=True, index=True)
    session_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=False
    )
    player_id = Column(String, ForeignKey("players.player_id"), nullable=False)
    question_id = Column(String, ForeignKey("questions.question_id"), nullable=False)
    player_answer = Column(String, nullable=False)  # A, B, C, D or text answer
    is_correct = Column(Boolean, nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow)


class GameSessionState(Base):
    """Track the current state of a game session"""

    __tablename__ = "game_session_states"

    session_code = Column(
        String, ForeignKey("game_sessions.session_code"), primary_key=True
    )
    current_question_index = Column(Integer, default=0)  # 0-based index
    current_question_id = Column(
        String, ForeignKey("questions.question_id"), nullable=True
    )
    is_active = Column(Boolean, default=True)
    is_waiting_for_players = Column(Boolean, default=True)
    total_questions = Column(Integer, nullable=False)
    ispublic = Column(Boolean, default=True)
    isstarted = Column(Boolean, default=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
