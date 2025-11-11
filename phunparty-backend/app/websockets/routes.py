"""
WebSocket routes for real-time game functionality
"""

import asyncio
from datetime import datetime
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
                "started_at": (
                    game_state_obj.started_at.isoformat()
                    if game_state_obj.started_at
                    else None
                ),
                "ended_at": (
                    game_state_obj.ended_at.isoformat()
                    if game_state_obj.ended_at
                    else None
                ),
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
        manager.update_heartbeat(websocket)
        await manager.send_personal_message({"type": "pong"}, websocket)

    elif message_type == "connection_ack":
        # Client acknowledging successful connection - mark as ready
        manager.mark_client_ready(websocket)
        manager.update_heartbeat(websocket)
        logger.info(f"Client acknowledged connection for session {session_code}")

    elif message_type == "submit_answer" and client_type == "mobile":
        # Player submitting an answer
        answer = data.get("answer")
        question_id = data.get("question_id")

        if answer and question_id and player_id:
            await game_handler.handle_player_answer(player_id, answer, question_id, db)
    elif message_type == "player_announce" and client_type == "mobile":
        # Mobile client announcing presence after connection (backup mechanism)
        player_data = data or {}
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "player_joined",
                "data": {
                    "player_id": player_data.get("player_id") or player_id,
                    "player_name": player_data.get("player_name"),
                    "player_photo": player_data.get("player_photo"),
                    "timestamp": player_data.get("timestamp")
                    or datetime.now().isoformat(),
                },
            },
            exclude_client_types=["mobile"],
            critical=True,
        )
        logger.info(
            f"📢 Processed player_announce for {player_data.get('player_name')}"
        )

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

    elif message_type == "get_question_with_options":
        # Request for a specific question with randomized options
        question_id = data.get("question_id")
        if question_id:
            await handle_get_question_with_options(websocket, question_id, db)
        else:
            await manager.send_personal_message(
                {"type": "error", "data": {"message": "Question ID required"}},
                websocket,
            )

    elif message_type == "broadcast_current_question" and client_type == "web":
        # Host wants to broadcast current question to all players
        await handle_broadcast_current_question(session_code, db)

    else:
        logger.warning(f"Unknown message type: {message_type} from {client_type}")


async def handle_get_question_with_options(
    websocket: WebSocket, question_id: str, db: Session
):
    """
    Handle request for a question with randomized options
    """
    try:
        from app.logic.game_logic import get_question_with_randomized_options

        question_data = get_question_with_randomized_options(db, question_id)

        await manager.send_personal_message(
            {"type": "question_with_options", "data": question_data}, websocket
        )

    except Exception as e:
        logger.error(f"Error getting question with options: {e}")
        await manager.send_personal_message(
            {"type": "error", "data": {"message": f"Failed to get question: {str(e)}"}},
            websocket,
        )


async def handle_broadcast_current_question(session_code: str, db: Session):
    """
    Handle request to broadcast the current question to all players
    """
    try:
        from app.logic.game_logic import broadcast_question_with_options

        # Get current game state
        game_state = get_game_session_state(db, session_code)
        if not game_state:
            logger.error(f"Game session {session_code} not found")
            return

        if not game_state.current_question_id:
            logger.error(f"No current question for session {session_code}")
            return

        # Broadcast the current question with options
        await broadcast_question_with_options(
            session_code, game_state.current_question_id, db
        )

    except Exception as e:
        logger.error(f"Error broadcasting current question: {e}")
        await manager.broadcast_to_session(
            session_code,
            {"type": "error", "data": {"message": "Failed to broadcast question"}},
        )


async def handle_game_start(session_code: str, game_handler, db: Session):
    """Handle game start event"""
    try:
        logger.info(f"🎮 Starting game for session {session_code}")

        # Wait for all connections to be ready before proceeding
        await manager.wait_for_ready_connections(session_code, timeout=2.0)

        # Update game state in database to mark as started
        from app.logic.game_logic import updateGameStartStatus, get_game_session_state

        updateGameStartStatus(db, session_code, True)

        # Get current game state to get the current question ID
        game_state = get_game_session_state(db, session_code)
        current_question = None

        # Step 1: Broadcast game_started event first (so UI transitions from lobby)
        logger.info(f"📡 Broadcasting game_started message for session {session_code}")
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_started",
                "data": {
                    "session_code": session_code,
                    "started_at": datetime.now().isoformat(),
                    "isstarted": True,
                    "game_state": {
                        "isstarted": True,
                        "is_active": game_state.is_active if game_state else True,
                        "current_question_index": (
                            game_state.current_question_index if game_state else 0
                        ),
                        "total_questions": (
                            game_state.total_questions if game_state else 1
                        ),
                    },
                },
            },
            critical=True,  # This is critical - retry if needed
        )

        # Step 2: Small delay to ensure game_started is processed before question
        await asyncio.sleep(0.5)

        # Step 3: Now broadcast the first question
        if game_state and game_state.current_question_id:
            logger.info(f"Broadcasting first question {game_state.current_question_id}")
            # Use the new broadcast system with randomized options
            if hasattr(game_handler, "broadcast_question_with_options"):
                await game_handler.broadcast_question_with_options(
                    game_state.current_question_id, db
                )
            else:
                # Fallback to old system
                current_question = get_current_question_details(db, session_code)
                if current_question and hasattr(game_handler, "start_question"):
                    await game_handler.start_question(current_question)
                elif current_question:
                    await game_handler.broadcast_question(current_question)

        # Step 4: Send a status update after question broadcast
        await asyncio.sleep(0.2)

        # Get player counts for accurate status
        from app.database.dbCRUD import (
            get_number_of_players_in_session,
            count_responses_for_question,
        )

        total_players = get_number_of_players_in_session(db, session_code)
        current_responses = 0
        if game_state and game_state.current_question_id:
            current_responses = count_responses_for_question(
                db, session_code, game_state.current_question_id
            )

        status_data = {
            "isstarted": True,
            "is_active": game_state.is_active if game_state else True,
            "current_question_index": (
                game_state.current_question_index if game_state else 0
            ),
            "total_questions": game_state.total_questions if game_state else 1,
            "is_waiting_for_players": (
                game_state.is_waiting_for_players if game_state else True
            ),
            # Frontend-compatible format
            "game_state": "active",
            "currentQuestion": (
                (game_state.current_question_index + 1) if game_state else 1
            ),
            "totalQuestions": game_state.total_questions if game_state else 1,
            "playersCount": total_players,
            "playersAnswered": current_responses,
        }

        logger.info(f"Broadcasting game_status_update for session {session_code}")
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_status_update",
                "data": status_data,
            },
            critical=True,
        )

        logger.info(f"Game start sequence complete for session {session_code}")

    except Exception as e:
        logger.error(f"Error starting game: {e}", exc_info=True)


async def handle_next_question(session_code: str, game_handler, db: Session):
    """Handle moving to next question"""
    try:
        # Use the game logic to advance to next question
        from app.logic.game_logic import check_and_advance_game, get_game_session_state

        # Get current game state
        game_state = get_game_session_state(db, session_code)
        if not game_state or not game_state.current_question_id:
            logger.error(f"No current question found for session {session_code}")
            return

        # Force advance to next question
        result = check_and_advance_game(
            db, session_code, game_state.current_question_id
        )

        # Get the updated game state
        updated_game_state = get_game_session_state(db, session_code)

        if updated_game_state and updated_game_state.current_question_id:
            # Broadcast the new question with randomized options
            if hasattr(game_handler, "broadcast_question_with_options"):
                await game_handler.broadcast_question_with_options(
                    updated_game_state.current_question_id, db
                )
            else:
                # Fallback to regular broadcast
                current_question = get_current_question_details(db, session_code)
                if current_question and hasattr(game_handler, "start_question"):
                    await game_handler.start_question(current_question)
                elif current_question:
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
