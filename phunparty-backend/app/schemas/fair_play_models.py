from datetime import datetime

from app.config import Base
from app.schemas.social_models import uuid_text
from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String,
                        UniqueConstraint)


class SessionPlayerFairPlay(Base):
    __tablename__ = "session_player_fair_play"

    id = Column(String, primary_key=True, default=uuid_text)
    session_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=False, index=True
    )
    player_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    strike_count = Column(Integer, default=0, nullable=False)
    is_kicked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "session_code",
            "player_id",
            name="uq_session_player_fair_play",
        ),
    )


class FairPlayViolation(Base):
    __tablename__ = "fair_play_violations"

    id = Column(String, primary_key=True, default=uuid_text)
    session_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=False, index=True
    )
    player_id = Column(
        String, ForeignKey("players.player_id"), nullable=False, index=True
    )
    question_id = Column(
        String, ForeignKey("questions.question_id"), nullable=False, index=True
    )
    reason = Column(String, nullable=True)
    occurred_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "session_code",
            "player_id",
            "question_id",
            name="uq_fair_play_violation_question",
        ),
    )
