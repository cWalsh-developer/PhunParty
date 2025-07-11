from sqlalchemy import Column, String
from app.config import Base

class Questions(Base):
    __tablename__ = "questions"
    question_id = Column(String, primary_key=True, index=False)
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)
    genre = Column(String, nullable=False)