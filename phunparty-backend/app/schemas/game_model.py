from app.config import Base
from sqlalchemy import Column, String


class Game(Base):
    __tablename__ = "games"
    game_code = Column(String, primary_key=True, index=False)
    genre = Column(String, nullable=False)
    rules = Column(String, nullable=False)
