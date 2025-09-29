"""
WebSocket routes for real-time game functionality
"""

import json
import logging
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.orm import Session

from app.database.dbCRUD import (
    get_current_question_details,
    get_game_session_state,
    get_player_by_ID,
    get_session_by_code,
)
from app.dependencies import get_db
from app.websockets.game_handlers import GAME_HANDLERS, create_game_handler
from app.websockets.manager import manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/session/{session_code}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_code: str,
    client_type: str = Query("web", description="Client type: 'web' or 'mobile'"),
    player_id: Optional[str] = Query(None, description="Player ID for mobile clients"),
    player_name: Optional[str] = Query(
        None, description="Player name for mobile clients"
    ),
    player_photo: Optional[str] = Query(
        None, description="Player photo URL for mobile clients"
    ),
):
    """
    WebSocket endpoint for game session communication

    Query parameters:
    - client_type: "web" for web UI, "mobile" for mobile app
    - player_id: Required for mobile clients
    - player_name: Display name for mobile clients
    - player_photo: Photo URL for mobile clients
    """
    db: Session = next(get_db())

    try:
        # Verify session exists
        session = get_session_by_code(db, session_code)
        if not session:
            await websocket.close(code=4004, reason="Session not found")
            return

        # For mobile clients, verify player exists
        if client_type == "mobile":
            if not player_id:
                await websocket.close(
                    code=4001, reason="Player ID required for mobile clients"
                )
                return

            player = get_player_by_ID(db, player_id)
            if not player:
                await websocket.close(code=4004, reason="Player not found")
                return

            # Use player info from database if not provided in query params
            player_name = player_name or player.player_name
            player_photo = player_photo or player.profile_photo_url

        # Connect to session
        await manager.connect(
            websocket=websocket,
            session_code=session_code,
            client_type=client_type,
            player_id=player_id,
            player_name=player_name,
            player_photo=player_photo,
        )

        # Send initial session state to the connecting client
        await send_initial_session_state(websocket, session_code, client_type, db)

        # Get game type for this session (you'll need to add this to your session model)
        game_type = (
            "trivia"  # Default - you can get this from session.game_type or similar
        )
        game_handler = create_game_handler(session_code, game_type)

        # Message handling loop
        while True:
            try:
                # Receive message from client
                data = await websocket.receive_text()
                message = json.loads(data)

                await handle_websocket_message(
                    message,
                    websocket,
                    session_code,
                    client_type,
                    player_id,
                    game_handler,
                    db,
                )

            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Invalid JSON format"})
                )
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Internal server error"})
                )

    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
        await websocket.close(code=4000, reason="Connection error")
    finally:
        manager.disconnect(websocket)


async def send_initial_session_state(
    websocket: WebSocket, session_code: str, client_type: str, db: Session
):
    """Send initial state when client connects"""
    try:
        # Get current session stats
        session_stats = manager.get_session_stats(session_code)

        # Get current game state and convert to dict for JSON serialization
        game_state_obj = get_game_session_state(db, session_code)
        game_state = None
        if game_state_obj:
            game_state = {
                "session_code": game_state_obj.session_code,
                "current_question_index": game_state_obj.current_question_index,
                "current_question_id": game_state_obj.current_question_id,
                "is_active": game_state_obj.is_active,
                "is_waiting_for_players": game_state_obj.is_waiting_for_players,
                "isstarted": game_state_obj.isstarted,
                "total_questions": game_state_obj.total_questions,
                "ispublic": game_state_obj.ispublic,
                "started_at": game_state_obj.started_at.isoformat() if game_state_obj.started_at else None,
                "ended_at": game_state_obj.ended_at.isoformat() if game_state_obj.ended_at else None,
            }

        initial_state = {
            "type": "initial_state",
            "data": {
                "session_code": session_code,
                "client_type": client_type,
                "connection_stats": session_stats,
                "game_state": game_state,
                "connected_players": manager.get_mobile_players(session_code),
            },
        }

        # Add current question if game is active
        if game_state and game_state.get("is_active"):
            try:
                current_question = get_current_question_details(db, session_code)
                if current_question:
                    initial_state["data"]["current_question"] = current_question
            except Exception as e:
                logger.warning(f"Could not get current question: {e}")

        await manager.send_personal_message(initial_state, websocket)

    except Exception as e:
        logger.error(f"Error sending initial session state: {e}")


async def handle_websocket_message(
    message: dict,
    websocket: WebSocket,
    session_code: str,
    client_type: str,
    player_id: Optional[str],
    game_handler,
    db: Session,
):
    """Handle incoming WebSocket messages"""
    message_type = message.get("type")
    data = message.get("data", {})

    logger.info(
        f"Received {message_type} from {client_type} client in session {session_code}"
    )

    if message_type == "ping":
        # Heartbeat/keepalive
        await manager.send_personal_message({"type": "pong"}, websocket)

    elif message_type == "submit_answer" and client_type == "mobile":
        # Player submitting an answer
        answer = data.get("answer")
        question_id = data.get("question_id")

        if answer and question_id and player_id:
            await game_handler.handle_player_answer(player_id, answer, question_id, db)

    elif message_type == "buzzer_press" and client_type == "mobile":
        # Player pressing buzzer (for buzzer games)
        if player_id and hasattr(game_handler, "handle_buzzer_press"):
            await game_handler.handle_buzzer_press(player_id, db)

    elif message_type == "start_game" and client_type == "web":
        # Web client starting the game
        await handle_game_start(session_code, game_handler, db)

    elif message_type == "next_question" and client_type == "web":
        # Web client moving to next question
        await handle_next_question(session_code, game_handler, db)

    elif message_type == "end_game" and client_type == "web":
        # Web client ending the game
        await handle_game_end(session_code, db)

    elif message_type == "get_session_stats":
        # Request for current session statistics
        stats = manager.get_session_stats(session_code)
        await manager.send_personal_message(
            {"type": "session_stats", "data": stats}, websocket
        )

    else:
        logger.warning(f"Unknown message type: {message_type} from {client_type}")


async def handle_game_start(session_code: str, game_handler, db: Session):
    """Handle game start event"""
    try:
        # Update game state in database to mark as started
        from app.logic.game_logic import updateGameStartStatus

        updateGameStartStatus(db, session_code, True)

        # Get first question
        current_question = get_current_question_details(db, session_code)

        if current_question:
            if hasattr(game_handler, "start_question"):
                await game_handler.start_question(current_question)
            else:
                await game_handler.broadcast_question(current_question)

        # Broadcast game started to all clients
        game_state = get_game_session_state(db, session_code)
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_started",
                "data": {
                    "session_code": session_code,
                    "started_at": "now",  # You can use proper datetime
                    "isstarted": game_state.isstarted if game_state else True,
                    "current_question": current_question,
                },
            },
        )

    except Exception as e:
        logger.error(f"Error starting game: {e}")


async def handle_next_question(session_code: str, game_handler, db: Session):
    """Handle moving to next question"""
    try:
        # Advance to next question in database
        # You'll need to implement this in dbCRUD
        # advance_to_next_question(db, session_code)

        # Get the new current question
        current_question = get_current_question_details(db, session_code)

        if current_question:
            if hasattr(game_handler, "start_question"):
                await game_handler.start_question(current_question)
            else:
                await game_handler.broadcast_question(current_question)
        else:
            # No more questions - end game
            await handle_game_end(session_code, db)

    except Exception as e:
        logger.error(f"Error advancing to next question: {e}")


async def handle_game_end(session_code: str, db: Session):
    """Handle game end event"""
    try:
        # Update game state in database
        # You'll need to implement this in dbCRUD
        # update_game_session_ended(db, session_code)

        # Get final scores (you'll need to implement this)
        # final_scores = get_final_scores(db, session_code)

        # Broadcast game ended to all clients
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_ended",
                "data": {
                    "session_code": session_code,
                    "ended_at": "now",  # You can use proper datetime
                    # "final_scores": final_scores
                },
            },
        )

    except Exception as e:
        logger.error(f"Error ending game: {e}")


# REST endpoints for WebSocket management
@router.get("/ws/sessions/{session_code}/stats")
async def get_session_websocket_stats(session_code: str):
    """Get WebSocket connection statistics for a session"""
    stats = manager.get_session_stats(session_code)
    return {"session_code": session_code, "stats": stats}


@router.post("/ws/sessions/{session_code}/broadcast")
async def broadcast_to_session(
    session_code: str, message: dict, client_type: Optional[str] = None
):
    """Broadcast a message to all clients in a session (admin endpoint)"""
    try:
        if client_type == "mobile":
            await manager.broadcast_to_mobile_players(session_code, message)
        elif client_type == "web":
            await manager.broadcast_to_web_clients(session_code, message)
        else:
            await manager.broadcast_to_session(session_code, message)

        return {"message": "Broadcast sent successfully"}
    except Exception as e:
        logger.error(f"Error broadcasting message: {e}")
        raise HTTPException(status_code=500, detail="Failed to broadcast message")
