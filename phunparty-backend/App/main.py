from fastapi import FastAPI
from app.config import Base, engine
from app.models.game_model import Game
from app.models.players_model import Players
from app.models.history_model import GameHistory
from app.models.questions_model import Questions
from app.routes import game

app = FastAPI(title="PhunParty Backend API")

app.include_router(game.router, 
                   prefix="/game", 
                   tags=[{"name": "Game",
                           "description": "Endpoints for managing game sessions, questions, and answers"}])


Base.metadata.create_all(bind=engine)
