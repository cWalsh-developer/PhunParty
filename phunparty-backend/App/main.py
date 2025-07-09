from fastapi import FastAPI
from App.Routes import game

app = FastAPI(title="PhunParty Backend API")

app.include_router(game.router, 
                   prefix="/game", 
                   tags=[{"name": "Game",
                           "description": "Endpoints for managing game sessions, questions, and answers"}])



