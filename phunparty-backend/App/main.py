from fastapi import FastAPI
from app.config import Base, engine
from app.models.game_model import Game
from app.models.players_model import Players
from app.models.game_session_model import GameSession
from app.models.session_player_assignment_model import SessionAssignment
from app.models.questions_model import Questions
from app.models.session_question_assignment import SessionQuestionAssignment
from app.models.scores_model import Scores
from app.routes import game
from app.routes import players
from app.routes import questions

app = FastAPI(title="PhunParty Backend API")

app.include_router(game.router, 
                   prefix="/game", 
                   tags=[{"name": "Game",
                           "description": "Endpoints for managing game sessions, questions, and answers"}])

app.include_router(players.router, 
                   prefix="/players", 
                   tags=[{"name": "Players",
                           "description": "Endpoints for managing players in the game"}])
"""
app.include_router(history.router, 
                   prefix="/history",
                         tags=[{"name": "History",
                                "description": "Endpoints for managing game history"}])

app.include_router(questions.router, 
                   prefix="/questions",
                         tags=[{"name": "Questions",
                                "description": "Endpoints for managing questions in each of the games"}])
"""

Base.metadata.create_all(bind=engine)
