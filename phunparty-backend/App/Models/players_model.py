from sqlalchemy import Column, JSON, String, ForeignKey, Integer
from app.config import Base

class Players(Base):
    __tablename__ = 'players'
    player_id = Column(String, primary_key=True, index=False)
    game_code = Column(String, ForeignKey('games.game_code'), nullable=True)
    player_name = Column(String, nullable=False)
    player_email = Column(String, nullable=False)
    player_mobile = Column(String, nullable=True)