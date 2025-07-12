from sqlalchemy import Column, String, ForeignKey, DateTime
from app.config import Base

class SessionAssignment(Base):
    __tablename__ = 'session_assignments'
    assignment_id = Column(String, primary_key=True, index=False)
    player_id = Column(String, ForeignKey('players.player_id'), nullable=False)
    session_code = Column(String, ForeignKey('game_sessions.session_code'), nullable=False)
    question_id = Column(String, ForeignKey('questions.question_id'), nullable=False)
    session_start = Column(DateTime, nullable=False)
    session_end = Column(DateTime, nullable=True)