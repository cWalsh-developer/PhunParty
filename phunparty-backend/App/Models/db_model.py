from sqlalchemy import Column, Integer, String, ForeignKey
from config import Base

class Game(Base):
    __tablename__ = 'games'
    game_code = Column(String, primary_key= True, index=False)
    host_name = Column(String, nullable=False)
    players = Column(String, nullable=True)  # Store as JSON string
    scores = Column(Integer, nullable=True)  # Store as Integer

