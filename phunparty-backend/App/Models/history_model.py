from sqlalchemy import Column, JSON, String, ForeignKey, Integer
from app.config import Base

class GameHistory(Base):
    __tablename__ = 'game_history'
    history_code = Column(String, primary_key=True, index=False)
    game_code = Column(String, ForeignKey('games.game_code'), nullable=False)
    players = Column(JSON, nullable=False)  # Store as JSON string
    results = Column(JSON, nullable=False)  # Store as JSON string
    date_played = Column(String, nullable=False)  # Store as ISO format string