from fastapi import FastAPI, Depends
from app.dependencies import get_db
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
from app.routes import scores

app = FastAPI(title="PhunParty Backend API")

app.include_router(
    game.router,
    prefix="/game",
    tags=[
        {
            "name": "Game",
            "description": "Endpoints for managing game sessions, questions, and answers",
        }
    ],
)

app.include_router(
    players.router,
    prefix="/players",
    tags=[
        {"name": "Players", "description": "Endpoints for managing players in the game"}
    ],
)

app.include_router(
    scores.router,
    prefix="/scores",
    tags=[
        {"name": "Scores", "description": "Endpoints for managing scores in the game"}
    ],
)

app.include_router(
    questions.router,
    prefix="/questions",
    tags=[
        {
            "name": "Questions",
            "description": "Endpoints for managing questions in each of the games",
        }
    ],
)

# Initialize database tables
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Warning: Could not create database tables: {e}")


@app.get("/")
def read_root():
    return {
        "message": "PhunParty Backend API - Welcome!",
        "version": "1.0.0",
        "description": "A fun party trivia game backend API",
        "documentation": "/docs",
        "available_endpoints": [
            {
                "entity": "Game Management",
                "base_path": "/game",
                "description": "Manage game sessions and game types",
                "endpoints": [
                    {
                        "method": "POST",
                        "endpoint": "/game/",
                        "description": "Create a new game type",
                        "example": "POST http://localhost:8000/game/",
                    },
                    {
                        "method": "POST",
                        "endpoint": "/game/create/session",
                        "description": "Create a new game session",
                        "example": "POST http://localhost:8000/game/create/session",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/{game_code}",
                        "description": "Get game details by game code",
                        "example": "GET http://localhost:8000/game/TRIVIA001",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/",
                        "description": "Get all available games",
                        "example": "GET http://localhost:8000/game/",
                    },
                    {
                        "method": "POST",
                        "endpoint": "/game/join",
                        "description": "Join an existing game session",
                        "example": "POST http://localhost:8000/game/join",
                    },
                ],
            },
            {
                "entity": "Player Management",
                "base_path": "/players",
                "description": "Manage players in the game",
                "endpoints": [
                    {
                        "method": "POST",
                        "endpoint": "/players/create",
                        "description": "Create a new player",
                        "example": "POST http://localhost:8000/players/create",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/players/{player_id}",
                        "description": "Get player details by ID",
                        "example": "GET http://localhost:8000/players/PLAYER123",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/players/",
                        "description": "Get all players",
                        "example": "GET http://localhost:8000/players/",
                    },
                    {
                        "method": "DELETE",
                        "endpoint": "/players/{player_id}",
                        "description": "Delete a player",
                        "example": "DELETE http://localhost:8000/players/PLAYER123",
                    },
                    {
                        "method": "PUT",
                        "endpoint": "/players/{player_id}/name",
                        "description": "Update player name",
                        "example": "PUT http://localhost:8000/players/PLAYER123/name",
                    },
                ],
            },
            {
                "entity": "Questions Management",
                "base_path": "/questions",
                "description": "Manage trivia questions",
                "endpoints": [
                    {
                        "method": "GET",
                        "endpoint": "/questions/{question_id}",
                        "description": "Get question by ID",
                        "example": "GET http://localhost:8000/questions/Q001",
                    },
                    {
                        "method": "POST",
                        "endpoint": "/questions/verify_answer",
                        "description": "Verify a player's answer to a question",
                        "example": "POST http://localhost:8000/questions/verify_answer",
                    },
                    {
                        "method": "POST",
                        "endpoint": "/questions/add",
                        "description": "Add a new question",
                        "example": "POST http://localhost:8000/questions/add",
                    },
                ],
            },
            {
                "entity": "Scores Management",
                "base_path": "/scores",
                "description": "Manage player scores and game results",
                "endpoints": [
                    {
                        "method": "GET",
                        "endpoint": "/scores/session/{session_code}",
                        "description": "Get scores for a game session",
                        "example": "GET http://localhost:8000/scores/session/SESSION123",
                    }
                ],
            },
        ],
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}
