"""
WebSocket routes for real-time game functionality
"""

import asyncio
from datetime import datetime, timedelta
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
            
            # CRITICAL: Check for excessive connections (safety limit)
            # The manager will close old connections, but this prevents abuse
            MAX_CONNECTIONS_PER_PLAYER = 3
            existing_count = manager.get_connection_count_for_player(
                session_code, player_id
            )
            
            if existing_count >= MAX_CONNECTIONS_PER_PLAYER:
                logger.error(
                    f"🚫 Player {player_id} has {existing_count} connections - rejecting (limit: {MAX_CONNECTIONS_PER_PLAYER})"
                )
                await websocket.close(
                    code=1008, 
                    reason=f"Too many connections ({existing_count}). Please refresh your app."
                )
                return

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

        # Add current question ONLY for web clients AND only if game has started
        # Mobile clients should NOT receive question in initial state - they get it via game_started flow
        if (
            client_type == "web"
            and game_state
            and game_state.get("is_active")
            and game_state.get("isstarted")
        ):
            try:
                current_question = get_current_question_details(db, session_code)
                if current_question:
                    initial_state["data"]["current_question"] = current_question
                    logger.info(
                        f"Included current question in initial state for web client"
                    )
            except Exception as e:
                logger.warning(f"Could not get current question: {e}")
        elif client_type == "mobile":
            # For mobile, explicitly indicate they should wait for game_started
            logger.info(
                f"Mobile client connecting - will receive question after game_started event"
            )

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
        # Heartbeat/keepalive with clock synchronization
        manager.update_heartbeat(websocket)

        # Include server time for client clock offset calculation
        server_time_ms = int(datetime.utcnow().timestamp() * 1000)
        client_sent_at = data.get("clientSentAt") if data else None

        pong_data = {
            "type": "pong",
            "serverTime": server_time_ms,  # Server UTC time in milliseconds
        }

        # Echo back client timestamp if provided for RTT calculation
        if client_sent_at:
            pong_data["clientSentAt"] = client_sent_at

        await manager.send_personal_message(pong_data, websocket)

    elif message_type == "connection_ack":
        # Client acknowledging successful connection - mark as ready
        manager.mark_client_ready(websocket)
        manager.update_heartbeat(websocket)
        logger.info(f"Client acknowledged connection for session {session_code}")

    elif message_type == "request_current_question" and client_type == "mobile":
        # Mobile client requesting current question from queue
        logger.info(
            f"📲 Mobile client requesting current question for session {session_code}"
        )
        current_question = manager.get_current_question(session_code)

        if current_question:
            logger.info(
                f"📤 Sending queued question {current_question.get('question_id')} to mobile client"
            )
            await manager.send_personal_message(
                {
                    "type": "question_started",
                    "data": current_question,
                },
                websocket,
            )
        else:
            logger.warning(f"⚠️ No queued question available for session {session_code}")
            # Fallback: try to get from database
            try:
                from app.database.dbCRUD import get_current_question_details

                game_status = get_current_question_details(db, session_code)
                if game_status and game_status.get("current_question"):
                    question_data = game_status["current_question"]
                    logger.info(
                        f"📤 Sending DB question {question_data.get('question_id')} to mobile client"
                    )
                    await manager.send_personal_message(
                        {
                            "type": "question_started",
                            "data": question_data,
                        },
                        websocket,
                    )
            except Exception as e:
                logger.error(f"Failed to get question from DB: {e}")

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

    elif message_type == "countdown_complete" and client_type == "web":
        # Web client signaling countdown has completed
        # THIS is the synchronized trigger for question reveal on all devices
        logger.info(
            f"⏱️ Countdown complete for session {session_code} - Synchronized question reveal"
        )

        from datetime import timedelta

        # Calculate synchronized start time - 500ms in the future
        # This gives all clients time to receive and schedule the display
        now = datetime.utcnow()
        start_at = now + timedelta(milliseconds=500)
        start_at_iso = start_at.isoformat() + "Z"

        logger.info(f"🕐 Question will display at: {start_at_iso} (500ms from now)")

        # First, broadcast sync pulse to prepare clients
        await manager.broadcast_to_session(
            session_code,
            {
                "type": "countdown_complete",
                "data": {
                    "ready_for_question": True,
                    "session_code": session_code,
                    "timestamp": datetime.now().isoformat(),
                    "start_at": start_at_iso,  # When to actually display
                    **data,  # Include any additional data from the client
                },
            },
            critical=True,
        )

        # Get the current question with synchronized start time
        from app.database.dbCRUD import get_current_question_details

        try:
            game_status = get_current_question_details(db, session_code)
            # Extract just the question object - don't send the entire game status!
            if game_status and game_status.get("current_question"):
                question_data = game_status["current_question"]

                # Add synchronized start_at timestamp to question data
                question_data["start_at"] = start_at_iso

                logger.info(
                    f"📤 Broadcasting synchronized question {question_data.get('question_id')} with start_at={start_at_iso}"
                )

                # Queue the question for late joiners and reconnections
                manager.queue_question(session_code, question_data)

                # Broadcast to ALL clients with synchronized timing
                await manager.broadcast_to_session(
                    session_code,
                    {
                        "type": "question_started",
                        "data": question_data,
                    },
                    critical=True,
                )

                logger.info(
                    f"✅ Question queued and broadcast - all clients will reveal at {start_at_iso}"
                )
            else:
                logger.warning(f"No current question found after countdown_complete")
        except Exception as e:
            logger.warning(f"Could not broadcast question after countdown: {e}")

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

        # CRITICAL: Ensure roster is synced before starting game
        # Step 1: Wait for all WebSocket connections to be ready
        await manager.wait_for_ready_connections(session_code, timeout=2.0)

        # Step 2: Verify roster synchronization between WebSocket and database
        from app.database.dbCRUD import get_number_of_players_in_session

        # Get connected mobile players from WebSocket
        mobile_connections = manager.get_mobile_players(session_code)
        ws_player_count = len(mobile_connections)

        # Get registered players from database
        db_player_count = get_number_of_players_in_session(db, session_code)

        logger.info(
            f"📊 Roster validation - WebSocket: {ws_player_count} players, Database: {db_player_count} players"
        )

        # If counts don't match, broadcast roster update and wait briefly
        if ws_player_count != db_player_count:
            logger.warning(
                f"⚠️ Roster mismatch detected! Broadcasting roster update to sync..."
            )
            await manager.broadcast_player_roster_update(session_code)

            # Give frontend time to update (small delay)
            await asyncio.sleep(0.5)

            # Re-check after roster update
            db_player_count = get_number_of_players_in_session(db, session_code)
            logger.info(
                f"📊 After roster sync - WebSocket: {ws_player_count}, Database: {db_player_count}"
            )

        # Update game state in database to mark as started
        from app.logic.game_logic import updateGameStartStatus, get_game_session_state

        updateGameStartStatus(db, session_code, True)

        # Get current game state to get the current question ID
        game_state = get_game_session_state(db, session_code)
        current_question = None

        # Get first question data to include in game_started event
        first_question_data = None
        if game_state and game_state.current_question_id:
            try:
                from app.logic.game_logic import get_question_with_randomized_options

                question_full = get_question_with_randomized_options(
                    db, game_state.current_question_id
                )

                # Determine ui_mode
                difficulty = question_full.get("difficulty", "").lower()
                ui_mode = "text_input"
                if (
                    question_full.get("display_options")
                    and len(question_full["display_options"]) > 0
                ):
                    if difficulty in ["easy", "medium"]:
                        ui_mode = "multiple_choice"
                    elif difficulty == "hard":
                        ui_mode = "text_input"

                first_question_data = {
                    "game_type": "trivia",  # CRITICAL: Mobile needs this field!
                    "question_id": question_full["question_id"],
                    "question": question_full["question"],
                    "genre": question_full["genre"],
                    "difficulty": question_full["difficulty"],
                    "display_options": question_full["display_options"],
                    "options": question_full["display_options"],
                    "ui_mode": ui_mode,
                }
                logger.info(
                    f"📝 Including first question in game_started: {question_full['question_id']}, ui_mode={ui_mode}, game_type=trivia"
                )
            except Exception as e:
                logger.error(f"Error getting first question for game_started: {e}")

        # Step 1: Broadcast game_started event WITHOUT question data
        # This prevents mobiles from rendering early before intro completes
        logger.info(f"📡 Broadcasting game_started message for session {session_code}")

        game_started_data = {
            "session_code": session_code,
            "started_at": datetime.now().isoformat(),
            "isstarted": True,
            "game_state": {
                "isstarted": True,
                "is_active": game_state.is_active if game_state else True,
                "current_question_index": (
                    game_state.current_question_index if game_state else 0
                ),
                "total_questions": (game_state.total_questions if game_state else 1),
            },
        }

        # DO NOT include question in game_started - prevents early mobile rendering
        # The question will be sent after countdown_complete for synchronized display

        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_started",
                "data": game_started_data,
            },
            critical=True,
        )

        # Step 2: Send web-only preload so host can prepare UI during intro
        # This is NOT visible to mobiles - prevents the race condition
        if first_question_data:
            logger.info(
                f"📺 Sending preload_question to WEB only (not visible to mobile yet)"
            )
            await manager.broadcast_to_session(
                session_code,
                {
                    "type": "preload_question",
                    "data": first_question_data,
                },
                only_client_types=["web"],
                critical=True,
            )
            logger.info(f"✅ Web host can now prepare question UI during intro")
        else:
            logger.warning("⚠️ No question data available for preload!")

        # Step 3: DO NOT broadcast question_started here!
        # The synchronized question reveal happens ONLY after countdown_complete
        # This ensures perfect timing between intro finish and question display
        logger.info(
            "⏸️ Question broadcast deferred until countdown_complete for synchronized reveal"
        )

        # Step 4: Send a status update after game_started
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


@router.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    try:
        active_sessions = len(manager.active_connections)
        total_connections = sum(len(connections) for connections in manager.active_connections.values())
        
        # Get memory usage
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024
        
        return {
            "status": "healthy",
            "active_sessions": active_sessions,
            "total_connections": total_connections,
            "memory_usage_mb": round(memory_mb, 2),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }
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
