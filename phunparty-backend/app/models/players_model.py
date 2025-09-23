from sqlalchemy import JSON, Column, ForeignKey, Integer, String

from app.config import Base


class Players(Base):
    __tablename__ = "players"
    player_id = Column(String, primary_key=True, index=False)
    player_name = Column(String, nullable=False)
    player_email = Column(String, nullable=False, unique=True)
    player_mobile = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    active_game_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=True
    )
