from app.config import Base
from app.models.enums import ResultType
from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String


class Scores(Base):
    __tablename__ = "scores"

    score_id = Column(String, primary_key=True, index=False)
    score = Column(Integer, nullable=False)
    result = Column(SAEnum(ResultType, name="result_types"), nullable=True)

    player_id = Column(String, ForeignKey("players.player_id"), nullable=False)

    session_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=False
    )

    # Public display snapshot for leaderboards/results.
    player_display_name = Column(String, nullable=True)
    player_photo_url = Column(String, nullable=True)
