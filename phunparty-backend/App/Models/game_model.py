from sqlalchemy import Column, JSON, String, ForeignKey, Integer
from app.config import Base

class Game(Base):
    __tablename__ = 'games'
    game_code = Column(String, primary_key= True, index=False)
    host_name = Column(String, nullable=False)
    genre = Column(String, nullable=False)
    rules = Column(String, nullable=False)
    players = Column(JSON, nullable=True)  # Store as JSON string
    scores = Column(JSON, nullable=True)  # Store as JSON string