from sqlalchemy import Column, ForeignKey, Integer, String

from app.config import Base


class GameSession(Base):
    __tablename__ = "game_sessions"
    session_code = Column(String, primary_key=True, index=False)
    host_name = Column(String, nullable=False)
    number_of_questions = Column(Integer, nullable=False)
    game_code = Column(String, ForeignKey("games.game_code"), nullable=False)
