from sqlalchemy import Column, ForeignKey, Integer, String

from app.config import Base


class GameSession(Base):
    __tablename__ = "game_sessions"
    session_code = Column(String, primary_key=True, index=False)
    host_name = Column(String, nullable=False)
    number_of_questions = Column(Integer, nullable=False)
    game_code = Column(String, ForeignKey("games.game_code"), nullable=False)
    owner_player_id = Column(
        String, ForeignKey("players.player_id"), nullable=True
    )  # Added for session ownership
    is_public = Column(Integer, default=1)  # Added to indicate if the session is public
