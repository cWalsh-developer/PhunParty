from fastapi import FastAPI
from app.config import Base, engine
from app.models.db_model import Game
from app.routes import game

app = FastAPI(title="PhunParty Backend API")

app.include_router(game.router, 
                   prefix="/game", 
                   tags=[{"name": "Game",
                           "description": "Endpoints for managing game sessions, questions, and answers"}])


Base.metadata.create_all(bind=engine)
