import logging
from typing import List

from app.database.dbCRUD import create_game as cg
from app.database.dbCRUD import create_game_session, end_game_session
from app.database.dbCRUD import get_all_games as gag
from app.database.dbCRUD import (
    get_all_public_sessions,
    get_game_by_code,
    get_game_history_for_player,
    get_player_private_sessions,
    get_session_by_code,
    get_session_details,
    join_game,
)
from app.dependencies import get_current_player, get_db, require_admin_api_key
from app.models.game import GameCreation, GameJoinRequest, GameSessionCreation
from app.models.response_models import GameHistoryResponse, GameResponse
from app.queue.join_queue_manager import join_queue_manager
from app.queue.queue_models import (
    JoinQueueRequest,
    JoinQueueResponse,
    QueueStatsResponse,
    QueueStatusResponse,
)
from app.schemas.players_model import Players
from app.schemas.scores_model import Scores
from app.security.cache import cache, invalidate_profile_cache
from app.security.ownership import (
    assert_public_or_member_or_owner,
    assert_same_player,
    assert_session_owner,
)
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from app.websockets.manager import manager
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", tags=["Game"])
def create_game(
    request: GameCreation,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_api_key),
):
    try:
        game = cg(db, request.rules, request.genre)
        cache.delete("games:list")
        return {
            "message": "Game created successfully.",
            "game_code": game.game_code,
            "rules": game.rules,
            "genre": game.genre,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create game")


@router.post("/create/session", tags=["Game"])
def create_game_session_route(
    request: GameSessionCreation,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Create a new game session with randomly selected questions.

    The difficulty parameter is optional:
    - If provided ('easy', 'medium', 'hard'), only questions of that difficulty will be selected
    - If not provided, questions of any difficulty will be randomly selected
    """
    try:
        gameSession = create_game_session(
            db,
            request.host_name,
            request.number_of_questions,
            request.game_code,
            current_player.player_id,
            request.ispublic,
            request.difficulty,
            request.beat_clock_duration_seconds
            or request.duration_seconds
            or request.timer_seconds
            or 60,
        )
        if request.ispublic:
            cache.delete("game:sessions:public")
        return {
            "session_code": gameSession.session_code,
            "host_name": gameSession.host_name,
            "number_of_questions": gameSession.number_of_questions,
            "game_code": gameSession.game_code,
            "owner_player_id": gameSession.owner_player_id,
            "beat_clock_duration_seconds": gameSession.beat_clock_duration_seconds,
            "difficulty": request.difficulty,
            "message": "Game session created successfully with randomly selected questions",
        }
    except ValueError as e:
        # Handle specific validation errors (like invalid difficulty or not enough questions)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create game session: {str(e)}"
        )


@router.get(
    "/history/{player_id}", response_model=List[GameHistoryResponse], tags=["Game"]
)
def get_player_game_history(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get the game history for a specific player.
    Returns a list of completed games with session_code, game_type (genre), and did_win (boolean).
    """
    try:
        assert_same_player(current_player, player_id)
        history = get_game_history_for_player(db, player_id)
        return history
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Unable to retrieve game history: {str(e)}"
        )


@router.get("/{game_code}", tags=["Game"])
def get_game(
    game_code: str,
    db: Session = Depends(get_db),
    current_player: Players = Depends(get_current_player),
):
    """
    Retrieve the game session details by game code.
    """
    try:
        game = get_game_by_code(db, game_code)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        return game
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve game information"
        )


@router.get("/", response_model=List[GameResponse], tags=["Game"])
def get_all_games(
    db: Session = Depends(get_db),
):
    """
    Retrieve all games.
    """
    try:
        cached = cache.get("games:list")
        if cached is not None:
            return cached

        games = gag(db)
        if not games:
            raise HTTPException(status_code=404, detail="No games found")
        response = [
            {
                "game_code": game.game_code,
                "genre": game.genre,
                "rules": game.rules,
            }
            for game in games
        ]
        cache.set("games:list", response, ttl_seconds=600)
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Unable to retrieve games list")


@router.post("/join", tags=["Game"])
async def join_game_route(
    request: Request,
    req: GameJoinRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Join an existing game session (direct join - may fail under high concurrency).
    For safer concurrent joins, use /join-queue endpoint instead.
    """
    try:
        await enforce_rate_limit(
            request,
            scope="game-join-ip",
            identifier=get_client_ip(request),
            limit=30,
            window_seconds=300,
        )
        game = join_game(db, req.session_code, current_player.player_id)
        return {
            "message": "Successfully joined the game!",
        }
    except ValueError as e:
        # Handle specific business logic errors
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        # Handle unexpected errors
        logger.exception(
            "Unexpected error joining game session=%s player=%s",
            req.session_code,
            current_player.player_id,
        )
        raise HTTPException(
            status_code=500, detail="Unable to join game - internal error"
        )


@router.post("/join-queue", response_model=JoinQueueResponse, tags=["Game"])
async def join_game_queue(
    request: JoinQueueRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Join a game session via queue system to prevent race conditions.
    This is the recommended way to join sessions when multiple players might join simultaneously.
    Returns a queue_id for tracking the join status.
    """
    try:
        # Ensure queue manager is running (start if not already running)
        if not join_queue_manager._running:
            try:
                await join_queue_manager.start()
            except Exception as e:
                return JoinQueueResponse(
                    success=False, message=f"Failed to start queue system: {str(e)}"
                )
        if not join_queue_manager._running:
            await join_queue_manager.start()

        # Add to queue
        queue_id = await join_queue_manager.add_to_queue(
            player_id=current_player.player_id,
            session_code=request.session_code,
            websocket_id=request.websocket_id,
        )

        # Estimate wait time (rough calculation based on queue position)
        queue_stats = join_queue_manager.get_queue_stats()
        estimated_wait = min(queue_stats["pending"] * 2, 30)  # Max 30 seconds

        return JoinQueueResponse(
            success=True,
            message="Added to join queue successfully",
            queue_id=queue_id,
            estimated_wait_time=estimated_wait,
        )

    except Exception as e:
        return JoinQueueResponse(
            success=False, message=f"Failed to add to join queue: {str(e)}"
        )


@router.get(
    "/queue-status/{queue_id}", response_model=QueueStatusResponse, tags=["Game"]
)
async def get_queue_status(queue_id: str):
    """
    Get the current status of a queue entry.
    """
    try:
        status = await join_queue_manager.get_queue_status(queue_id)

        if not status:
            return QueueStatusResponse(success=False, message="Queue entry not found")

        return QueueStatusResponse(
            success=True, message="Queue status retrieved successfully", **status
        )

    except Exception as e:
        return QueueStatusResponse(
            success=False, message=f"Failed to get queue status: {str(e)}"
        )


@router.get("/queue-stats", response_model=QueueStatsResponse, tags=["Game"])
async def get_queue_stats(_: str = Depends(require_admin_api_key)):
    """
    Get current queue statistics (for debugging/monitoring).
    """
    try:
        stats = join_queue_manager.get_queue_stats()

        return QueueStatsResponse(
            success=True, message="Queue statistics retrieved successfully", stats=stats
        )

    except Exception as e:
        return QueueStatsResponse(
            success=False, message=f"Failed to get queue statistics: {str(e)}"
        )


@router.get("/session/{session_code}/join-info", tags=["Game"])
def get_session_join_info(
    session_code: str,
    request: Request,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get session join information for WebSocket connection.
    """
    try:
        import os

        # Verify session exists
        session = get_session_by_code(db, session_code)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        assert_public_or_member_or_owner(db, current_player, session_code)

        # Prefer explicit deployment URLs, but in local development derive the
        # socket host from the actual request. This prevents local mobile
        # clients from receiving a production wss://api.phun.party URL.
        api_url = (os.getenv("API_URL") or str(request.base_url)).rstrip("/")
        web_url = (
            os.getenv("WEB_URL")
            or request.headers.get("origin")
            or "https://phun.party"
        ).rstrip("/")

        # Convert https to wss for WebSocket URL
        ws_url = api_url.replace("https://", "wss://").replace("http://", "ws://")

        return {
            "session_code": session_code,
            "host_name": session.host_name,
            "game_code": session.game_code,
            "number_of_questions": session.number_of_questions,
            "websocket_url": f"{ws_url}/ws/session/{session_code}",
            "web_join_url": f"{web_url}/#/join/{session_code}",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve session information"
        )


@router.get("/session/{session_code}/details", tags=["Game"])
def get_session_details_route(
    session_code: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get comprehensive session information including session code, genre,
    number of questions, active status, and privacy status.
    """
    try:
        session_details = get_session_details(db, session_code)
        if not session_details:
            raise HTTPException(status_code=404, detail="Session not found")
        assert_public_or_member_or_owner(db, current_player, session_code)
        return session_details
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve session details"
        )


@router.get("/sessions/public", tags=["Game"])
def get_all_public_sessions_route(
    db: Session = Depends(get_db),
    current_player: Players = Depends(get_current_player),
):
    """
    Get all public active sessions (available to everyone).
    Returns: session_code, genre, number_of_questions, difficulty
    """
    try:
        cached = cache.get("game:sessions:public")
        if cached is not None:
            return cached

        sessions = get_all_public_sessions(db)
        response = {"sessions": sessions, "count": len(sessions)}
        cache.set("game:sessions:public", response, ttl_seconds=10)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve public sessions"
        )


@router.get("/sessions/private/{player_id}", tags=["Game"])
def get_player_private_sessions_route(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get all private active sessions owned by a specific player.
    Returns: session_code, genre, number_of_questions, difficulty
    """
    try:
        assert_same_player(current_player, player_id)
        sessions = get_player_private_sessions(db, player_id)
        return {"player_id": player_id, "sessions": sessions, "count": len(sessions)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve private sessions"
        )


@router.post("/end-game/{session_code}", tags=["Game"])
async def end_game_route(
    session_code: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    End a game session.
    """
    try:
        assert_session_owner(db, current_player, session_code)
        result = end_game_session(db, session_code)
        cache.delete("game:sessions:public")
        score_player_ids = [
            player_id
            for (player_id,) in db.query(Scores.player_id)
            .filter(Scores.session_code == session_code)
            .all()
            if player_id
        ]
        for player_id in score_player_ids:
            invalidate_profile_cache(player_id)

        # Broadcast game ended message to all connected WebSocket clients
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_ended",
                "data": result,
            },
        )

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to end game session")
