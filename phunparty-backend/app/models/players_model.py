from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Boolean
from datetime import datetime

from app.config import Base


class Players(Base):
    __tablename__ = "players"
    player_id = Column(String, primary_key=True, index=False)
    player_name = Column(String, nullable=True)  # Nullable for deleted accounts
    player_email = Column(
        String, nullable=True, unique=True
    )  # Nullable for deleted accounts
    player_mobile = Column(String, nullable=True)
    hashed_password = Column(String, nullable=True)  # Nullable for deleted accounts
    profile_photo_url = Column(String, nullable=True)
    active_game_code = Column(
        String, ForeignKey("game_sessions.session_code"), nullable=True
    )
    is_deactivated = Column(Boolean, default=False, nullable=False)
    deactivated_at = Column(String, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(String, nullable=True)
