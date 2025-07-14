from sqlalchemy import Column, Enum, String, ForeignKey, Integer
from app.config import Base

class Scores(Base):
    results_enum = Enum('win', 'lose', 'draw', name='results_enum')
    __tablename__ = 'scores'
    score_id = Column(String, primary_key=True, index=False)
    score = Column(Integer, nullable=False)
    result = Column(results_enum, nullable= True)
    player_id = Column(String, ForeignKey('players.player_id'), nullable=False)
    session_code = Column(String, ForeignKey('game_sessions.session_code'), nullable=False)