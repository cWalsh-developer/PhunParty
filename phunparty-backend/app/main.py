import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import Base, engine
from app.schemas.game_model import Game
from app.schemas.game_session_model import GameSession
from app.schemas.fair_play_models import FairPlayViolation, SessionPlayerFairPlay
from app.schemas.game_state_models import GameSessionState, PlayerResponse
from app.schemas.passwordReset import PasswordReset
from app.schemas.players_model import Players
from app.schemas.questions_model import Questions
from app.schemas.scores_model import Scores
from app.schemas.session_player_assignment_model import SessionAssignment
from app.schemas.session_question_assignment import SessionQuestionAssignment
from app.schemas.social_models import (
    FriendRequest,
    Friendship,
    Notification,
    UserPushToken,
)
from app.database.social_migrations import ensure_social_player_columns
from app.database.fair_play_migrations import ensure_fair_play_columns
from app.routes import (
    authentication,
    friends,
    game,
    game_logic,
    notifications,
    passwordReset,
    photos,
    players,
    questions,
    scores,
)
from app.websockets import routes as websocket_routes
from app.security.rate_limit import enforce_rate_limit, get_client_ip, rate_limiter

logger = logging.getLogger(__name__)


"""PhunParty Backend API main application module.

Author: Connor Walsh

This module creates and configures the FastAPI application instance, includes routers for game
management, players, scores, questions, authentication, password reset, photos, and WebSockets.
It also mounts static files for uploads, initializes database tables, and defines root and
health check endpoints.
"""


def warn_about_websocket_process_state():
    """Warn when deployment hints suggest multiple in-memory WebSocket managers."""
    worker_hints = " ".join(
        [
            os.getenv("WEB_CONCURRENCY", ""),
            os.getenv("GUNICORN_CMD_ARGS", ""),
        ]
    )
    if (
        "--workers 1" in worker_hints
        or "--workers=1" in worker_hints
        or "-w 1" in worker_hints
        or worker_hints.strip() == "1"
    ):
        return

    logger.warning(
        "WebSocket session, phase, ACK, and roster state is in process memory. "
        "Run this backend with one worker until that state moves to Redis/pub-sub."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        ensure_fair_play_columns()
        ensure_social_player_columns()
    except Exception as e:
        logger.warning("Could not create database tables: %s", e)

    await rate_limiter.connect()
    warn_about_websocket_process_state()
    try:
        yield
    finally:
        await rate_limiter.close()


app = FastAPI(title="PhunParty Backend API", lifespan=lifespan)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "https://phun.party,http://localhost:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def global_rate_limit(request: Request, call_next):
    if request.url.path not in {"/health"}:
        await enforce_rate_limit(
            request,
            scope="global-ip",
            identifier=get_client_ip(request),
            limit=300,
            window_seconds=60,
        )

    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

    return response


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

app.include_router(
    game_logic.router,
    prefix="/game-logic",
    tags=[
        {
            "name": "Game Logic",
            "description": "Endpoints for game progression and automatic advancement",
        }
    ],
)

app.include_router(
    authentication.router,
    prefix="/auth",
    tags=[
        {
            "name": "Authentication",
            "description": "Endpoints for player authentication and login",
        }
    ],
)

app.include_router(
    passwordReset.router, prefix="/password-reset", tags=["Password Reset"]
)

app.include_router(
    photos.router,
    prefix="/photos",
    tags=[
        {
            "name": "Photos",
            "description": "Endpoints for managing player profile photos",
        }
    ],
)

app.include_router(
    friends.router,
    prefix="/friends",
    tags=[{"name": "Friends", "description": "Friend requests and friendships"}],
)

app.include_router(
    notifications.router,
    prefix="/notifications",
    tags=[
        {
            "name": "Notifications",
            "description": "In-app notifications and push notification settings",
        }
    ],
)

app.include_router(
    websocket_routes.router,
    tags=[
        {
            "name": "WebSockets",
            "description": "WebSocket endpoints for real-time game functionality",
        }
    ],
)

# Mount static files for serving photos
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def read_root():
    """Root endpoint providing an overview of the PhunParty Backend API.

    Returns:
        dict: API information including message, version, description, documentation link,
        and a detailed list of available endpoints grouped by entity.
    """
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
                    {
                        "method": "GET",
                        "endpoint": "/game/history/{player_id}",
                        "description": "Get game history for a player",
                        "example": "GET http://localhost:8000/game/history/PLAYER123",
                    },
                    {
                        "method": "POST",
                        "endpoint": "/game/join-queue",
                        "description": "Join the game queue when multiple players are joining at once",
                        "example": "POST http://localhost:8000/game/join-queue",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/queue-status/{queue_id}",
                        "description": "Get the status of a game queue",
                        "example": "GET http://localhost:8000/game/queue-status/QUEUE123",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/queue-stats",
                        "description": "Get statistics about current game queues",
                        "example": "GET http://localhost:8000/game/queue-stats",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/sessions/public",
                        "description": "Get all public game sessions",
                        "example": "GET http://localhost:8000/game/sessions/public",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/sessions/private/{player_id}",
                        "description": "Get details about a specific game session for a player",
                        "example": "GET http://localhost:8000/game/sessions/private/PLAYER123",
                    },
                    {
                        "method": "POST",
                        "endpoint": "/game/end-game/{session_code}",
                        "description": "End a game session",
                        "example": "POST http://localhost:8000/game/end-game/SESSION123",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/sessions/{session_code}/details",
                        "description": "Get details about a specific game session",
                        "example": "GET http://localhost:8000/game/sessions/SESSION123/details",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game/sessions/{session_code}/join-info",
                        "description": "Get join information for a specific game session",
                        "example": "GET http://localhost:8000/game/sessions/SESSION123/join-info",
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
                        "endpoint": "/players/{player_id}",
                        "description": "Update player profile",
                        "example": "PUT http://localhost:8000/players/PLAYER123",
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
                        "endpoint": "/scores/{session_code}",
                        "description": "Get scores for a game session",
                        "example": "GET http://localhost:8000/scores/3ERH4I225",
                    }
                ],
            },
            {
                "entity": "Game Logic & Progression",
                "base_path": "/game-logic",
                "description": "Handle automatic game progression and player responses",
                "endpoints": [
                    {
                        "method": "POST",
                        "endpoint": "/game-logic/submit-answer",
                        "description": "Submit a player's answer (auto-advances game when all players answer)",
                        "example": "POST http://localhost:8000/game-logic/submit-answer",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game-logic/status/{session_code}",
                        "description": "Get current game status and progression",
                        "example": "GET http://localhost:8000/game-logic/status/SESSION123",
                    },
                    {
                        "method": "GET",
                        "endpoint": "/game-logic/current-question/{session_code}",
                        "description": "Get the current question for a session",
                        "example": "GET http://localhost:8000/game-logic/current-question/SESSION123",
                    },
                    {
                        "method": "PUT",
                        "endpoint": "/game-logic/start-game/{session_code}",
                        "description": "Update the game's started status",
                        "example": "PUT http://localhost:8000/game-logic/start-game/{session_code",
                    },
                ],
            },
            {
                "entity": "Authentication",
                "base_path": "/auth",
                "description": "User authentication endpoints",
                "endpoints": [
                    {
                        "method": "POST",
                        "endpoint": "/auth/login",
                        "description": "Login a player",
                        "example": "POST http://localhost:8000/auth/login",
                    },
                ],
            },
            {
                "entity": "Password Reset",
                "base_path": "/password-reset",
                "description": "Password reset via OTP",
                "endpoints": [
                    {
                        "method": "POST",
                        "endpoint": "/password-reset/request",
                        "description": "Request a password reset OTP",
                        "example": "POST http://localhost:8000/password-reset/request",
                    },
                ],
                "endpoints": [
                    {
                        "method": "POST",
                        "endpoint": "/password-reset/verify",
                        "description": "Verify the OTP received via SMS",
                        "example": "POST http://localhost:8000/password-reset/verify",
                    },
                ],
                "endpoints": [
                    {
                        "method": "PUT",
                        "endpoint": "/password-reset/update",
                        "description": "Update password using verified OTP",
                        "example": "PUT http://localhost:8000/password-reset/update",
                    },
                ],
            },
        ],
    }


@app.get("/health")
def health_check():
    """Health check endpoint to verify API status.

    Returns:
        dict: Status indicating the API is healthy.
    """
    return {"status": "healthy"}
