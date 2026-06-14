from app.config import Base
from sqlalchemy import Column, ForeignKey, Integer, String


class GameSession(Base):
    __tablename__ = "game_sessions"
    session_code = Column(String, primary_key=True, index=False)
    host_name = Column(String, nullable=False)
    number_of_questions = Column(Integer, nullable=False)
    game_code = Column(String, ForeignKey("games.game_code"), nullable=False)
    owner_player_id = Column(
        String, ForeignKey("players.player_id"), nullable=True
    )  # Added for session ownership
    beat_clock_duration_seconds = Column(Integer, nullable=False, default=60)
