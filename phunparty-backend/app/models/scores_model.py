from sqlalchemy import Column, Enum, ForeignKey, Integer, String

from app.config import Base
from app.models.enums import ResultType


class Scores(Base):
    __tablename__ = "scores"
    score_id = Column(String, primary_key=True, index=False)
    score = Column(Integer, nullable=False)
    result = Column(Enum(ResultType), default=None, nullable=True)
    player_id = Column(String, ForeignKey("players.player_id"), nullable=False)
    session_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=False
    )
