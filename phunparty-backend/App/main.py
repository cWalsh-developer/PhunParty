from fastapi import FastAPI
from config import Base, engine
from models.db_model import Game
from routes import game

app = FastAPI(title="PhunParty Backend API")

app.include_router(game.router, 
                   prefix="/game", 
                   tags=[{"name": "Game",
                           "description": "Endpoints for managing game sessions, questions, and answers"}])


Base.metadata.create_all(bind=engine)
