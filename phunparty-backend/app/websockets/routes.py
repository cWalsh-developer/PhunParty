"""
WebSocket routes for real-time game functionality
"""

import asyncio
from collections.abc import Generator
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
from app.database.fair_play_crud import (
    get_fair_play_record,
    get_eligible_player_ids_for_session,
    has_focus_violation_for_question,
    is_player_kicked,
    record_focus_violation,
    update_fair_play_settings,
)
from app.dependencies import get_db
from app.logic.game_logic import check_and_advance_game
from app.websockets.game_handlers import create_game_handler
from app.websockets.game_lifecycle import handle_game_end
from app.websockets.game_modes import BUZZER_GAME_TYPE, resolve_session_game_type
from app.websockets.manager import SessionPhase, manager
from app.websockets.scheduler import (
    COUNTDOWN_DURATION_MS,
    NEXT_QUESTION_REVEAL_DELAY_MS,
    advance_or_end_current_question,
    format_buzzer_question_for_mobile,
    iso_utc,
    reveal_current_question,
    start_countdown,
)

logger = logging.getLogger(__name__)
router = APIRouter()
INTRO_RECOVERY_WINDOW_SECONDS = 15
FAIR_PLAY_GRACE_PERIOD_MS = 1500


def parse_optional_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return bool(value)


def open_db_session() -> tuple[Session, Generator[Session, None, None]]:
    """Open a short-lived DB session from the FastAPI dependency generator."""
    db_generator = get_db()
    db = next(db_generator)
    return db, db_generator


def close_db_session(db_generator: Generator[Session, None, None]) -> None:
    try:
        db_generator.close()
    except Exception:
        logger.exception("Error closing websocket DB session")


def build_player_fair_play_status(
    db: Session, session_code: str, player_id: str
) -> dict:
    game_state = get_game_session_state(db, session_code)
    record = get_fair_play_record(db, session_code, player_id)

    # If the live game state has already been cleaned/deleted, fall back to the
    # terminal snapshot kept by the websocket manager.
    if not game_state:
        terminal_session = manager.get_terminal_session(session_code)

        if terminal_session:
            terminal_statuses = terminal_session.get("fair_play_player_status", {})
            player_status = terminal_statuses.get(player_id)

            removed_players = terminal_session.get("removed_players", []) or []
            removed_match = next(
                (
                    removed
                    for removed in removed_players
                    if str(removed.get("player_id")) == str(player_id)
                ),
                None,
            )

            if player_status or removed_match:
                status_source = {
                    **(player_status or {}),
                    **(removed_match or {}),
                }

                is_kicked = bool(
                    status_source.get("is_kicked")
                    or status_source.get("answer_status") == "kicked"
                    or removed_match
                )

                max_strikes = int(status_source.get("max_strikes") or 3)
                strike_count = int(
                    status_source.get("strike_count")
                    or (max_strikes if is_kicked else 0)
                )

                return {
                    "player_id": player_id,
                    "session_code": session_code,
                    "phase": terminal_session.get("phase", "ended"),
                    "ended_at": terminal_session.get("ended_at"),
                    "strike_count": strike_count,
                    "max_strikes": max_strikes,
                    "is_kicked": is_kicked,
                    "is_frozen": is_kicked or bool(status_source.get("is_frozen")),
                    "frozen_question_id": status_source.get("frozen_question_id"),
                    "reason": status_source.get("reason")
                    or ("fair_play_strikes" if is_kicked else None),
                    "fair_play_reason": status_source.get("fair_play_reason")
                    or status_source.get("reason")
                    or ("fair_play_strikes" if is_kicked else None),
                    "answer_status": status_source.get("answer_status")
                    or ("kicked" if is_kicked else None),
                    "message": status_source.get("message")
                    or (
                        f"You were removed after {max_strikes} Fair Play strikes."
                        if is_kicked
                        else None
                    ),
                    "terminal": True,
                }

            # The session ended and we have a terminal snapshot, but this player
            # was not removed.
            return {
                "player_id": player_id,
                "session_code": session_code,
                "phase": terminal_session.get("phase", "ended"),
                "ended_at": terminal_session.get("ended_at"),
                "strike_count": 0,
                "max_strikes": 3,
                "is_kicked": False,
                "is_frozen": False,
                "frozen_question_id": None,
                "reason": None,
                "fair_play_reason": None,
                "answer_status": None,
                "message": None,
                "terminal": True,
            }

        # If the DB fair-play record survived but the game state did not, still
        # return useful information instead of 404.
        if record:
            max_strikes = 3
            is_kicked = bool(record.is_kicked)
            strike_count = record.strike_count if record else 0

            return {
                "player_id": player_id,
                "session_code": session_code,
                "phase": "ended",
                "strike_count": strike_count,
                "max_strikes": max_strikes,
                "is_kicked": is_kicked,
                "is_frozen": is_kicked,
                "frozen_question_id": None,
                "reason": "fair_play_strikes" if is_kicked else None,
                "fair_play_reason": "fair_play_strikes" if is_kicked else None,
                "answer_status": "kicked" if is_kicked else None,
                "message": (
                    f"You were removed after {max_strikes} Fair Play strikes."
                    if is_kicked
                    else None
                ),
                "terminal": True,
            }

        raise HTTPException(status_code=404, detail="Session not found")

    max_strikes = getattr(game_state, "max_fair_play_strikes", 3) or 3
    current_question_id = getattr(game_state, "current_question_id", None)
    is_frozen = bool(
        current_question_id
        and has_focus_violation_for_question(
            db, session_code, player_id, current_question_id
        )
    )
    is_kicked = bool(record and record.is_kicked)
    strike_count = record.strike_count if record else 0
    answer_status = "kicked" if is_kicked else ("frozen" if is_frozen else None)
    reason = "fair_play_strikes" if is_kicked else None
    message = (
        f"You were removed after {max_strikes} Fair Play strikes."
        if is_kicked
        else None
    )

    return {
        "player_id": player_id,
        "session_code": session_code,
        "strike_count": strike_count,
        "max_strikes": max_strikes,
        "is_kicked": is_kicked,
        "is_frozen": is_frozen or is_kicked,
        "frozen_question_id": current_question_id if is_frozen else None,
        "reason": reason,
        "fair_play_reason": reason,
        "answer_status": answer_status,
        "message": message,
    }


@router.get("/fair-play/session/{session_code}/player/{player_id}/status")
def get_player_fair_play_status(
    session_code: str,
    player_id: str,
    db: Session = Depends(get_db),
):
    return build_player_fair_play_status(db, session_code, player_id)


def serialize_game_state(
    game_state_obj, game_type: Optional[str] = None
) -> Optional[dict]:
    """Convert DB game state to a WebSocket-safe dict."""
    if not game_state_obj:
        return None

    serialized = {
        "session_code": game_state_obj.session_code,
        "current_question_index": game_state_obj.current_question_index,
        "current_question_id": game_state_obj.current_question_id,
        "is_active": game_state_obj.is_active,
        "is_waiting_for_players": game_state_obj.is_waiting_for_players,
        "isstarted": game_state_obj.isstarted,
        "fair_play_enabled": getattr(game_state_obj, "fair_play_enabled", False),
        "cheat_detection_enabled": getattr(game_state_obj, "fair_play_enabled", False),
        "max_fair_play_strikes": getattr(game_state_obj, "max_fair_play_strikes", 3),
        "max_cheat_strikes": getattr(game_state_obj, "max_fair_play_strikes", 3),
        "total_questions": game_state_obj.total_questions,
        "ispublic": game_state_obj.ispublic,
        "started_at": (
            game_state_obj.started_at.isoformat() if game_state_obj.started_at else None
        ),
        "ended_at": (
            game_state_obj.ended_at.isoformat() if game_state_obj.ended_at else None
        ),
    }
    if game_type:
        serialized["game_type"] = game_type
    return serialized


def build_sync_state(
    session_code: str, db: Session, game_type: Optional[str] = None
) -> dict:
    """Build authoritative state for initial load and reconnect recovery."""
    game_state_obj = get_game_session_state(db, session_code)
    game_type = game_type or resolve_session_game_type(db, session_code)
    game_state = serialize_game_state(game_state_obj, game_type)
    sync_state = manager.get_session_sync_state(session_code)

    if game_state:
        if game_state.get("ended_at") and sync_state.get("phase") != "ended":
            phase_state = manager.set_session_phase(
                session_code,
                SessionPhase.ENDED,
                current_question_id=game_state.get("current_question_id"),
                current_question_index=game_state.get("current_question_index"),
                total_questions=game_state.get("total_questions"),
            )
            sync_state.update(phase_state)
        elif game_state.get("isstarted") and sync_state.get("phase") == "lobby":
            started_at = game_state_obj.started_at if game_state_obj else None
            seconds_since_start = None
            if started_at:
                seconds_since_start = (
                    datetime.utcnow() - started_at.replace(tzinfo=None)
                ).total_seconds()

            is_fresh_intro = game_state.get("current_question_index", 0) == 0 and (
                seconds_since_start is not None
                and seconds_since_start <= INTRO_RECOVERY_WINDOW_SECONDS
            )
            recovery_phase = (
                SessionPhase.INTRO_AUDIO if is_fresh_intro else SessionPhase.QUESTION
            )
            phase_updates = {
                "current_question_id": game_state.get("current_question_id"),
                "current_question_index": game_state.get("current_question_index"),
                "total_questions": game_state.get("total_questions"),
            }
            if recovery_phase == SessionPhase.QUESTION:
                phase_updates["start_at"] = iso_utc(datetime.utcnow())

            phase_state = manager.set_session_phase(
                session_code,
                recovery_phase,
                **phase_updates,
            )
            sync_state.update(phase_state)

    sync_state["game_state"] = game_state
    sync_state["game_type"] = game_type
    sync_state["connected_players"] = manager.get_mobile_players(session_code)
    sync_state["current_question"] = get_mobile_current_question_payload(
        session_code, db, game_type
    )
    return sync_state


def get_mobile_current_question_payload(
    session_code: str, db: Session, game_type: Optional[str] = None
) -> Optional[dict]:
    """Return a recovery-safe current question payload for mobile clients."""
    queued_question = manager.get_current_question(session_code)
    if queued_question:
        return queued_question

    phase_state = manager.get_session_phase_state(session_code)
    if phase_state.get("phase") != SessionPhase.QUESTION.value:
        return None

    game_status = get_current_question_details(db, session_code)
    question_data = game_status.get("current_question") if game_status else None
    if not question_data:
        return None

    question_id = question_data.get("question_id")
    expected_question_id = phase_state.get("current_question_id")
    if expected_question_id and question_id != expected_question_id:
        logger.warning(
            f"Refusing current question fallback for {session_code}; DB question {question_id} != phase question {expected_question_id}"
        )
        return None

    game_type = game_type or resolve_session_game_type(db, session_code)
    question_data = {
        **question_data,
        "game_type": game_type,
        "start_at": phase_state.get("start_at"),
        "expires_at": phase_state.get("question_expires_at"),
        "duration_ms": phase_state.get("question_duration_ms"),
        "phase": phase_state.get("phase"),
        "server_time_ms": phase_state.get("server_time_ms"),
    }

    payload = (
        format_buzzer_question_for_mobile(question_data)
        if game_type == BUZZER_GAME_TYPE
        else question_data
    )
    manager.queue_question(session_code, payload)
    logger.info(
        f"Rebuilt queued current question {payload.get('question_id')} for mobile recovery in session {session_code}"
    )
    return payload


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
    game_type: Optional[str] = Query(
        None, description="Optional game mode override, e.g. 'trivia' or 'buzzer'"
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
    game_handler = None
    resolved_game_type = None

    try:
        db, db_generator = open_db_session()
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
                if is_player_kicked(db, session_code, player_id):
                    await websocket.close(
                        code=4003,
                        reason="Removed after Fair Play strikes",
                    )
                    return

                # Use player info from database if not provided in query params
                player_name = player_name or player.player_name
                player_photo = player_photo or player.profile_photo_url

                # CRITICAL: Check for existing connections from this player
                # Prevent duplicate registrations that cause the 7x join bug
                existing_connections = manager.get_player_connections(
                    session_code, player_id
                )

                if existing_connections:
                    logger.warning(
                        f"Player {player_id} ({player_name}) already has {len(existing_connections)} connection(s) to session {session_code}"
                    )
                    logger.info(
                        f"Closing {len(existing_connections)} old connection(s) before establishing new one"
                    )

                    # Remove old connections from manager state immediately so roster is stable.
                    manager.disconnect_player_by_id(session_code, player_id)

                    # Close old sockets in background to avoid blocking new connection setup.
                    for old_ws_id, old_conn_info in existing_connections.items():
                        try:
                            old_ws = old_conn_info.get("websocket")
                            if old_ws:
                                asyncio.create_task(
                                    old_ws.close(
                                        code=1000, reason="New connection established"
                                    )
                                )
                                logger.info(
                                    f"Closed old connection {old_ws_id} for player {player_id}"
                                )
                        except Exception as e:
                            logger.error(f"Error closing old connection: {e}")

                    logger.info(
                        f"Cleanup complete - ready for new connection from {player_name}"
                    )

            resolved_game_type = resolve_session_game_type(
                db, session_code, session=session, requested_game_type=game_type
            )
        finally:
            close_db_session(db_generator)

        # Connect to session after DB validation has completed.
        await manager.connect(
            websocket=websocket,
            session_code=session_code,
            client_type=client_type,
            player_id=player_id,
            player_name=player_name,
            player_photo=player_photo,
        )

        # Log connection stats for monitoring
        total_connections = manager.get_total_connection_count()
        session_connections = len(manager.active_connections.get(session_code, {}))
        logger.info(
            f"Connection stats - Session: {session_connections}, Total: {total_connections}"
        )

        # Send initial session state with its own short-lived DB session.
        initial_db, initial_db_generator = open_db_session()
        try:
            await send_initial_session_state(
                websocket,
                session_code,
                client_type,
                initial_db,
                game_type=resolved_game_type,
                player_id=player_id,
            )
        finally:
            close_db_session(initial_db_generator)

        game_handler = create_game_handler(session_code, resolved_game_type)
        logger.info(
            f"Created {resolved_game_type} game handler for session {session_code}"
        )

        # Message handling loop
        while True:
            try:
                # Receive message from client
                data = await websocket.receive_text()
                message = json.loads(data)

                message_db, message_db_generator = open_db_session()
                try:
                    await handle_websocket_message(
                        message,
                        websocket,
                        session_code,
                        client_type,
                        player_id,
                        game_handler,
                        message_db,
                    )
                finally:
                    close_db_session(message_db_generator)

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
        if client_type == "mobile" and player_id:
            disconnect_db, disconnect_db_generator = open_db_session()
            try:
                await handle_mobile_disconnect_during_fair_play(
                    session_code=session_code,
                    player_id=player_id,
                    db=disconnect_db,
                )
            finally:
                close_db_session(disconnect_db_generator)

        manager.disconnect(websocket)


async def send_initial_session_state(
    websocket: WebSocket,
    session_code: str,
    client_type: str,
    db: Session,
    game_type: Optional[str] = None,
    player_id: Optional[str] = None,
):
    """Send initial state when client connects"""
    try:
        # Get current session stats
        session_stats = manager.get_session_stats(session_code)

        # Get current game state and convert to dict for JSON serialization
        game_state_obj = get_game_session_state(db, session_code)
        game_type = game_type or resolve_session_game_type(db, session_code)
        game_state = serialize_game_state(game_state_obj, game_type)
        authoritative_state = build_sync_state(session_code, db, game_type)

        initial_state = {
            "type": "initial_state",
            "data": {
                "session_code": session_code,
                "client_type": client_type,
                "connection_stats": session_stats,
                "game_type": game_type,
                "game_state": game_state,
                "connected_players": manager.get_mobile_players(session_code),
                "authoritative_state": authoritative_state,
            },
        }
        if client_type == "mobile" and player_id:
            fair_play_status = {
                **manager.get_fair_play_status(session_code, player_id),
                "player_id": player_id,
            }
            initial_state["data"]["player_fair_play_status"] = fair_play_status
            initial_state["data"]["authoritative_state"][
                "player_fair_play_status"
            ] = fair_play_status

        # Include current question for web clients only.
        # Mobile clients must stay synchronized with scheduled question_started.
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
                        "Included current question in initial state for web client"
                    )
            except Exception as e:
                logger.warning(f"Could not get current question: {e}")
        elif client_type == "mobile":
            # For mobile, explicitly indicate they should wait for game_started
            logger.info(
                f"Mobile client connecting - will receive question after game_started event"
            )

        await manager.send_personal_message(initial_state, websocket)

        if client_type == "mobile":
            authoritative_phase = authoritative_state.get("phase")
            queued_question = get_mobile_current_question_payload(
                session_code, db, game_type
            )
            if authoritative_phase == SessionPhase.QUESTION.value and queued_question:
                logger.info(
                    f"Sending queued current question to reconnecting mobile in session {session_code}"
                )
                await manager.send_personal_critical_message(
                    session_code,
                    {
                        "type": "question_started",
                        "data": queued_question,
                    },
                    websocket,
                )

        # For host clients, send an immediate follow-up roster update after initial state
        # This ensures the web UI has the most current roster in case players joined
        # while the web client was connecting
        if client_type != "mobile":
            # Small delay to ensure initial_state is processed first
            await asyncio.sleep(0.05)
            await manager.broadcast_player_roster_update(session_code)
            logger.info(
                f"ðŸ“‹ Sent follow-up roster update to newly connected web client"
            )

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

    log_message = (
        logger.debug if message_type in {"ping", "pong", "ack"} else logger.info
    )
    log_message(
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

    elif message_type == "pong":
        # Client responding to server ping - update heartbeat
        manager.update_heartbeat(websocket)
        # No response needed for pong

    elif message_type == "connection_ack":
        # Client acknowledging successful connection - mark as ready
        manager.mark_client_ready(websocket)
        manager.update_heartbeat(websocket)
        logger.debug(f"Client acknowledged connection for session {session_code}")

    elif message_type == "ack":
        event_id = (
            message.get("event_id")
            or message.get("message_id")
            or data.get("event_id")
            or data.get("message_id")
        )
        if event_id:
            manager.acknowledge_event(websocket, event_id)
            manager.update_heartbeat(websocket)
        else:
            logger.warning(f"ACK missing event_id from {client_type} client")

    elif message_type == "sync_request":
        manager.update_heartbeat(websocket)
        await manager.send_personal_message(
            {
                "type": "sync_state",
                "data": build_sync_state(session_code, db),
            },
            websocket,
        )

    elif message_type == "request_current_question" and client_type == "mobile":
        # Mobile client requesting current question from queue
        logger.info(
            f"ðŸ“² Mobile client requesting current question for session {session_code}"
        )
        current_question = get_mobile_current_question_payload(session_code, db)

        if current_question:
            logger.info(
                f"ðŸ“¤ Sending queued question {current_question.get('question_id')} to mobile client"
            )
            await manager.send_personal_critical_message(
                session_code,
                {
                    "type": "question_started",
                    "data": current_question,
                },
                websocket,
            )
        else:
            logger.warning(
                f"âš ï¸ No queued question available for session {session_code}"
            )
            # The DB fallback above only runs once the authoritative server phase
            # is already question, so unanswered pre-reveal questions stay hidden.

    elif message_type == "leave_game" and client_type == "mobile":
        if not player_id:
            return

        connections = manager.get_player_connections(session_code, player_id)
        player_name = manager.get_player_name_from_websocket(websocket)
        manager.intentional_leaves.add(
            manager._player_task_key(session_code, player_id)
        )
        manager.disconnect_player_by_id(session_code, player_id)

        await manager.broadcast_to_session(
            session_code,
            {
                "type": "player_left",
                "data": {
                    "player_id": player_id,
                    "player_name": player_name,
                    "reason": "left_game",
                    "timestamp": datetime.now().isoformat(),
                },
            },
            exclude_client_types=["mobile"],
            critical=True,
        )
        await manager.broadcast_player_roster_update(session_code)

        for conn_info in connections.values():
            old_ws = conn_info.get("websocket")
            if old_ws:
                try:
                    await old_ws.close(code=1000, reason="Player left game")
                except Exception as e:
                    logger.debug(f"Error closing left player websocket: {e}")

    elif (
        message_type
        in {"focus_violation", "fair_play_focus_lost", "fair_play_window_violation"}
        and client_type == "mobile"
    ):
        if message_type == "fair_play_window_violation" and not data.get("reason"):
            data = {**data, "reason": "multi_window_mode"}
        await handle_fair_play_focus_lost(
            websocket=websocket,
            session_code=session_code,
            player_id=player_id,
            data=data,
            db=db,
        )

    elif message_type == "fair_play_focus_returned" and client_type == "mobile":
        await handle_fair_play_focus_returned(
            websocket=websocket,
            session_code=session_code,
            player_id=player_id,
            data=data,
        )

    elif message_type == "submit_answer" and client_type == "mobile":
        # Player submitting an answer
        answer = data.get("answer")
        question_id = data.get("question_id")

        if answer and question_id and player_id:
            if manager.is_player_frozen_for_question(
                session_code, player_id, question_id
            ) or is_player_kicked(db, session_code, player_id):
                await manager.send_personal_message(
                    {
                        "type": "answer_rejected",
                        "data": {
                            "reason": "fair_play_restriction",
                            "question_id": question_id,
                            "is_frozen": manager.is_player_frozen_for_question(
                                session_code, player_id, question_id
                            ),
                            "is_kicked": is_player_kicked(db, session_code, player_id),
                        },
                    },
                    websocket,
                )
                return
            await game_handler.handle_player_answer(player_id, answer, question_id, db)
    elif message_type == "player_announce" and client_type == "mobile":
        # Mobile client announcing presence after connection (backup mechanism)
        # Use authoritative roster sync to avoid duplicate/missed join states.
        player_data = data or {}
        await manager.broadcast_player_roster_update(session_code)
        logger.info(
            f"ðŸ“¢ Processed player_announce for {player_data.get('player_name')} with roster sync"
        )

    elif message_type == "request_roster" and client_type != "mobile":
        # Web client requesting current player roster (e.g., if they think they're out of sync)
        logger.info(f"ðŸ“‹ Web client requesting roster for session {session_code}")
        await manager.broadcast_player_roster_update(session_code)

    elif message_type == "buzzer_press" and client_type == "mobile":
        # Player pressing buzzer (for buzzer games)
        if player_id and hasattr(game_handler, "handle_buzzer_press"):
            phase_state = manager.get_session_phase_state(session_code)
            current_question_id = phase_state.get("current_question_id")
            incoming_question_id = data.get("question_id")
            if not incoming_question_id or incoming_question_id != current_question_id:
                logger.info(
                    "Ignoring stale buzzer press session=%s player=%s incoming=%s current=%s",
                    session_code,
                    player_id,
                    incoming_question_id,
                    current_question_id,
                )
                return
            if (
                current_question_id
                and manager.is_player_frozen_for_question(
                    session_code, player_id, current_question_id
                )
            ) or is_player_kicked(db, session_code, player_id):
                await manager.send_personal_message(
                    {
                        "type": "buzzer_rejected",
                        "data": {
                            "reason": "fair_play_restriction",
                            "question_id": current_question_id,
                            "is_frozen": bool(
                                current_question_id
                                and manager.is_player_frozen_for_question(
                                    session_code, player_id, current_question_id
                                )
                            ),
                            "is_kicked": is_player_kicked(db, session_code, player_id),
                        },
                    },
                    websocket,
                )
                return
            await game_handler.handle_buzzer_press(player_id, db, incoming_question_id)

    elif message_type == "start_game" and client_type == "web":
        # Web client starting the game
        await handle_game_start(session_code, game_handler, db)

    elif message_type == "update_session_settings" and client_type != "mobile":
        await handle_update_session_settings(session_code, data, db)

    elif message_type == "intro_complete" and client_type == "web":
        current_phase = manager.get_session_phase_state(session_code).get("phase")
        if current_phase != SessionPhase.INTRO_AUDIO.value:
            logger.info(
                f"Ignoring intro_complete for {session_code}; current phase is {current_phase}"
            )
            manager.update_heartbeat(websocket)
            return

        countdown_duration_ms = (
            data.get("duration_ms", COUNTDOWN_DURATION_MS)
            if data
            else COUNTDOWN_DURATION_MS
        )
        logger.info(
            f"Starting countdown for {session_code}: reason=intro_complete duration_ms={countdown_duration_ms} data={data}"
        )
        game_state = get_game_session_state(db, session_code)
        await start_countdown(
            session_code,
            duration_ms=countdown_duration_ms,
            reason="intro_complete",
            current_question_id=(
                game_state.current_question_id if game_state else None
            ),
            current_question_index=(
                game_state.current_question_index if game_state else None
            ),
            total_questions=(game_state.total_questions if game_state else None),
        )

    elif message_type == "skip_intro" and client_type == "web":
        current_phase = manager.get_session_phase_state(session_code).get("phase")
        if current_phase != SessionPhase.INTRO_AUDIO.value:
            logger.info(
                f"Ignoring skip_intro for {session_code}; current phase is {current_phase}"
            )
            manager.update_heartbeat(websocket)
            return

        await manager.broadcast_to_session(
            session_code,
            {
                "type": "intro_skipped",
                "data": {
                    **manager.get_session_phase_state(session_code),
                    "skipped_at": iso_utc(datetime.utcnow()),
                },
            },
            critical=True,
        )
        countdown_duration_ms = (
            data.get("duration_ms", COUNTDOWN_DURATION_MS)
            if data
            else COUNTDOWN_DURATION_MS
        )
        logger.info(
            f"Starting countdown for {session_code}: reason=skip_intro duration_ms={countdown_duration_ms} data={data}"
        )
        game_state = get_game_session_state(db, session_code)
        await start_countdown(
            session_code,
            duration_ms=countdown_duration_ms,
            reason="skip_intro",
            current_question_id=(
                game_state.current_question_id if game_state else None
            ),
            current_question_index=(
                game_state.current_question_index if game_state else None
            ),
            total_questions=(game_state.total_questions if game_state else None),
        )

    elif message_type == "countdown_complete" and client_type == "web":
        logger.info(
            f"Received countdown_complete for {session_code}; server scheduler owns reveal"
        )
        manager.update_heartbeat(websocket)
        return

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
        # Legacy host command: route through synchronized countdown.
        await handle_broadcast_current_question(session_code, db)

    else:
        logger.warning(f"Unknown message type: {message_type} from {client_type}")


async def handle_update_session_settings(session_code: str, data: dict, db: Session):
    """Update host-controlled session settings and broadcast them to clients."""
    try:
        fair_play_enabled = data.get("fair_play_enabled")
        if fair_play_enabled is None:
            fair_play_enabled = data.get("cheat_detection_enabled")

        max_strikes = data.get("max_fair_play_strikes")
        if max_strikes is None:
            max_strikes = data.get("max_cheat_strikes")

        game_state = update_fair_play_settings(
            db,
            session_code,
            fair_play_enabled=parse_optional_bool(fair_play_enabled),
            max_fair_play_strikes=max_strikes,
        )

        await manager.broadcast_to_session(
            session_code,
            {
                "type": "fair_play_settings_updated",
                "data": {
                    "session_code": session_code,
                    "fair_play_enabled": game_state.fair_play_enabled,
                    "cheat_detection_enabled": game_state.fair_play_enabled,
                    "max_fair_play_strikes": game_state.max_fair_play_strikes,
                    "max_cheat_strikes": game_state.max_fair_play_strikes,
                },
            },
            critical=True,
        )
    except ValueError as e:
        logger.warning(f"Could not update Fair Play settings for {session_code}: {e}")
    except Exception:
        logger.exception(f"Error updating Fair Play settings for {session_code}")


async def handle_mobile_disconnect_during_fair_play(
    session_code: str,
    player_id: str,
    db: Session,
):
    """Treat an ungraceful mobile disconnect during a Fair Play question as focus loss."""
    if manager._player_task_key(session_code, player_id) in manager.intentional_leaves:
        return

    game_state = get_game_session_state(db, session_code)
    if not game_state or not getattr(game_state, "fair_play_enabled", False):
        return
    if is_player_kicked(db, session_code, player_id):
        return

    phase_state = manager.get_session_phase_state(session_code)
    if phase_state.get("phase") != SessionPhase.QUESTION.value:
        return

    question_id = phase_state.get("current_question_id")
    if not question_id:
        return

    if manager.get_pending_focus_loss(session_code, player_id):
        return

    lost_at = iso_utc(datetime.utcnow())
    manager.record_pending_focus_loss(
        session_code=session_code,
        player_id=player_id,
        question_id=question_id,
        reason="mobile_disconnected_during_question",
        lost_at=lost_at,
    )
    logger.info(
        "FAIR PLAY DISCONNECT LOSS session=%s player=%s question=%s lost_at=%s",
        session_code,
        player_id,
        question_id,
        lost_at,
    )
    asyncio.create_task(
        finalize_focus_loss_after_grace(
            session_code=session_code,
            player_id=player_id,
            question_id=question_id,
            lost_at=lost_at,
        )
    )


async def handle_fair_play_focus_lost(
    websocket: WebSocket,
    session_code: str,
    player_id: Optional[str],
    data: dict,
    db: Session,
):
    logger.warning(
        "FAIR PLAY FOCUS LOST HANDLER HIT session=%s player=%s data=%s",
        session_code,
        player_id,
        data,
    )
    """Start a backend-owned Fair Play grace window for focus loss."""
    if not player_id:
        return

    game_state = get_game_session_state(db, session_code)
    if not game_state or not getattr(game_state, "fair_play_enabled", False):
        return

    phase_state = manager.get_session_phase_state(session_code)
    if phase_state.get("phase") != SessionPhase.QUESTION.value:
        return

    question_id = data.get("question_id")
    current_question_id = phase_state.get("current_question_id")
    if not question_id or question_id != current_question_id:
        logger.info(
            f"Ignoring focus loss for {session_code}/{player_id}; question {question_id} != {current_question_id}"
        )
        return

    reason = data.get("reason") or "left_question_screen"
    lost_at = data.get("occurred_at") or iso_utc(datetime.utcnow())

    immediate_violation_reasons = {"multi_window_mode", "picture_in_picture_mode"}

    if reason in immediate_violation_reasons:
        logger.info(
            "FAIR PLAY IMMEDIATE STRIKE session=%s player=%s question=%s reason=%s",
            session_code,
            player_id,
            question_id,
            reason,
        )

        await handle_focus_violation(
            websocket=websocket,
            session_code=session_code,
            player_id=player_id,
            data={
                "question_id": question_id,
                "reason": reason,
                "occurred_at": lost_at,
            },
            db=db,
        )
        return

    logger.info(
        "FAIR PLAY LOST session=%s player=%s question=%s reason=%s lost_at=%s",
        session_code,
        player_id,
        question_id,
        reason,
        lost_at,
    )
    existing_pending = manager.get_pending_focus_loss(session_code, player_id)

    if (
        existing_pending
        and existing_pending.get("question_id") == question_id
        and existing_pending.get("reason") == reason
    ):
        logger.info(
            "Ignoring duplicate pending Fair Play focus loss: session=%s player=%s question=%s reason=%s original_lost_at=%s duplicate_lost_at=%s",
            session_code,
            player_id,
            question_id,
            reason,
            existing_pending.get("lost_at"),
            lost_at,
        )
        return

    pending = manager.record_pending_focus_loss(
        session_code=session_code,
        player_id=player_id,
        question_id=question_id,
        reason=reason,
        lost_at=lost_at,
    )
    asyncio.create_task(
        finalize_focus_loss_after_grace(
            session_code=session_code,
            player_id=player_id,
            question_id=question_id,
            lost_at=lost_at,
        )
    )
    await manager.send_personal_message(
        {
            "type": "fair_play_focus_grace_started",
            "data": {
                **pending,
                "grace_period_ms": FAIR_PLAY_GRACE_PERIOD_MS,
            },
        },
        websocket,
    )


async def resync_buzzer_ui_after_fair_play_return(
    session_code: str,
    player_id: str,
    question_id: str | None,
    db: Session,
) -> None:
    """
    When a player returns within the Fair Play grace period, resend the current
    authoritative buzzer UI. This prevents the mobile from showing stale active
    buzzer state when the player should still be answering/waiting.
    """
    try:
        game_type = resolve_session_game_type(db, session_code)

        if game_type != BUZZER_GAME_TYPE:
            return

        phase_state = manager.get_session_phase_state(session_code)
        current_question_id = phase_state.get("current_question_id")

        if question_id and current_question_id and question_id != current_question_id:
            logger.info(
                "Skipping buzzer UI resync after Fair Play return for stale question: session=%s player=%s incoming=%s current=%s",
                session_code,
                player_id,
                question_id,
                current_question_id,
            )
            return

        buzzer_handler = create_game_handler(session_code, BUZZER_GAME_TYPE)

        await manager.broadcast_buzzer_state_update(session_code)

        if hasattr(buzzer_handler, "update_mobile_buzzer_ui"):
            await buzzer_handler.update_mobile_buzzer_ui(db)

        logger.info(
            "Resynced buzzer UI after Fair Play return: session=%s player=%s question=%s",
            session_code,
            player_id,
            current_question_id,
        )

    except Exception:
        logger.exception(
            "Failed to resync buzzer UI after Fair Play return: session=%s player=%s question=%s",
            session_code,
            player_id,
            question_id,
        )


async def handle_fair_play_focus_returned(
    websocket: WebSocket,
    session_code: str,
    player_id: Optional[str],
    data: dict,
):
    """Clear a pending Fair Play focus loss if the player returns in time."""
    if not player_id:
        return

    pending = manager.get_pending_focus_loss(session_code, player_id)
    logger.info(
        "FAIR PLAY RETURNED session=%s player=%s question=%s pending=%s",
        session_code,
        player_id,
        data.get("question_id"),
        bool(pending),
    )
    if data.get("question_id") and pending:
        if pending.get("question_id") != data.get("question_id"):
            return

    cleared = manager.clear_pending_focus_loss(session_code, player_id)
    if cleared:
        manager.update_fair_play_status(
            session_code,
            player_id,
            connection_state="connected",
            answer_status=None,
        )
        await manager.broadcast_player_roster_update(session_code)
        db, db_generator = open_db_session()
        try:
            await resync_buzzer_ui_after_fair_play_return(
                session_code=session_code,
                player_id=player_id,
                question_id=cleared.get("question_id"),
                db=db,
            )
        finally:
            db_generator.close()

        await manager.send_personal_message(
            {
                "type": "fair_play_focus_grace_cleared",
                "data": {
                    **cleared,
                    "returned_at": data.get("returned_at")
                    or iso_utc(datetime.utcnow()),
                },
            },
            websocket,
        )


async def finalize_focus_loss_after_grace(
    session_code: str,
    player_id: str,
    question_id: str,
    lost_at: str,
):
    await asyncio.sleep(FAIR_PLAY_GRACE_PERIOD_MS / 1000)

    pending = manager.get_pending_focus_loss(session_code, player_id)
    logger.info(
        "FAIR PLAY GRACE FINALIZE session=%s player=%s question=%s pending=%s",
        session_code,
        player_id,
        question_id,
        bool(pending),
    )
    if not pending:
        return

    if pending.get("question_id") != question_id or pending.get("lost_at") != lost_at:
        return

    manager.clear_pending_focus_loss(session_code, player_id)
    db, db_generator = open_db_session()
    try:
        await handle_focus_violation(
            websocket=None,
            session_code=session_code,
            player_id=player_id,
            data={
                "question_id": question_id,
                "reason": pending.get("reason") or "left_question_screen",
                "occurred_at": lost_at,
            },
            db=db,
        )
    finally:
        db_generator.close()


async def schedule_absent_player_fair_play_checks(
    session_code: str,
    question_id: str,
    grace_ms: int = FAIR_PLAY_GRACE_PERIOD_MS,
):
    """Strike eligible players who are still absent shortly after a question starts."""
    await asyncio.sleep(grace_ms / 1000)
    phase_state = manager.get_session_phase_state(session_code)
    if (
        phase_state.get("phase") != SessionPhase.QUESTION.value
        or phase_state.get("current_question_id") != question_id
    ):
        return

    db, db_generator = open_db_session()
    try:
        game_state = get_game_session_state(db, session_code)
        if not game_state or not getattr(game_state, "fair_play_enabled", False):
            return

        active_player_ids = {
            player.get("player_id")
            for player in manager.get_mobile_players(session_code)
            if player.get("player_id")
        }
        eligible_player_ids = get_eligible_player_ids_for_session(db, session_code)
        for player_id in eligible_player_ids:
            if player_id in active_player_ids:
                continue

            await handle_focus_violation(
                websocket=None,
                session_code=session_code,
                player_id=player_id,
                data={
                    "question_id": question_id,
                    "reason": "not_present_when_question_started",
                    "occurred_at": iso_utc(datetime.utcnow()),
                },
                db=db,
            )
    finally:
        db_generator.close()


async def advance_after_fair_play_if_ready(
    session_code: str, question_id: str, db: Session
):
    game_progression = check_and_advance_game(db, session_code, question_id)
    await manager.broadcast_to_session(
        session_code,
        {
            "type": "game_status_update",
            "data": game_progression,
        },
        critical=True,
    )

    action = game_progression.get("action")
    if action == "next_question":
        updated_game_state = get_game_session_state(db, session_code)
        if updated_game_state and updated_game_state.current_question_id:
            manager.clear_question_queue(session_code)
            question_start_at = datetime.utcnow() + timedelta(
                milliseconds=NEXT_QUESTION_REVEAL_DELAY_MS
            )
            await reveal_current_question(
                session_code,
                db,
                iso_utc(question_start_at),
            )
    elif action == "game_ended":
        await handle_game_end(session_code, db)

    return game_progression


async def apply_buzzer_fair_play_freeze(
    session_code: str,
    player_id: str,
    question_id: str,
    db: Session,
) -> None:
    """
    Keep buzzer mode moving when a Fair Play violation freezes a player.

    If the frozen player was the current buzzer winner, clear the winner and
    reopen buzzing so the remaining players can attempt the question.
    """
    try:
        game_type = resolve_session_game_type(db, session_code)

        if game_type != BUZZER_GAME_TYPE:
            return

        state = manager.get_buzzer_state(session_code)

        if state.get("current_question_id") != question_id:
            logger.info(
                "Skipping buzzer Fair Play freeze for stale question: session=%s player=%s incoming=%s current=%s",
                session_code,
                player_id,
                question_id,
                state.get("current_question_id"),
            )
            return

        frozen_players = state.setdefault("frozen_players", set())
        frozen_players.add(player_id)

        was_current_winner = state.get("current_buzzer_winner") == player_id
        has_current_winner = bool(state.get("current_buzzer_winner"))

        if was_current_winner:
            logger.info(
                "Fair Play froze current buzzer winner; reopening buzzes: session=%s player=%s question=%s",
                session_code,
                player_id,
                question_id,
            )

            state["current_buzzer_winner"] = None
            state["question_active"] = True
            state["transitioning"] = False
            state["accepting_buzzes"] = True

        elif not has_current_winner:
            logger.info(
                "Fair Play froze non-winner while buzzer open; keeping buzzes active: session=%s player=%s question=%s",
                session_code,
                player_id,
                question_id,
            )

            state["question_active"] = True
            state["transitioning"] = False
            state["accepting_buzzes"] = True

        else:
            logger.info(
                "Fair Play froze non-winner while another player is answering; keeping current buzzer winner: session=%s player=%s winner=%s question=%s",
                session_code,
                player_id,
                state.get("current_buzzer_winner"),
                question_id,
            )

        await manager.broadcast_buzzer_state_update(session_code)

        buzzer_handler = create_game_handler(session_code, BUZZER_GAME_TYPE)

        if hasattr(buzzer_handler, "update_mobile_buzzer_ui"):
            await buzzer_handler.update_mobile_buzzer_ui(
                db,
                message_override=(
                    "Another player was frozen by Fair Play. Buzzing is open again!"
                    if was_current_winner
                    else None
                ),
            )

    except Exception:
        logger.exception(
            "Failed to apply buzzer Fair Play freeze: session=%s player=%s question=%s",
            session_code,
            player_id,
            question_id,
        )


async def handle_focus_violation(
    websocket: Optional[WebSocket],
    session_code: str,
    player_id: Optional[str],
    data: dict,
    db: Session,
):
    """Validate and record a mobile focus-loss violation for Fair Play Mode."""
    if not player_id:
        return

    game_state = get_game_session_state(db, session_code)
    if not game_state or not getattr(game_state, "fair_play_enabled", False):
        return

    phase_state = manager.get_session_phase_state(session_code)
    if phase_state.get("phase") != SessionPhase.QUESTION.value:
        return

    question_id = data.get("question_id")
    current_question_id = phase_state.get("current_question_id")
    if not question_id or question_id != current_question_id:
        logger.info(
            f"Ignoring focus violation for {session_code}/{player_id}; question {question_id} != {current_question_id}"
        )
        return

    max_strikes = getattr(game_state, "max_fair_play_strikes", 3) or 3
    try:
        record, _violation, response_voided = record_focus_violation(
            db,
            session_code=session_code,
            player_id=player_id,
            question_id=question_id,
            reason=data.get("reason"),
            occurred_at=data.get("occurred_at"),
            max_strikes=max_strikes,
        )
    except ValueError:
        return

    reason = data.get("reason") or "left_question_screen"
    manager.freeze_player_for_question(session_code, player_id, question_id)
    manager.set_player_answered(session_code, player_id, True)
    player = get_player_by_ID(db, player_id)
    player_name = (
        manager.get_player_name_from_websocket(websocket)
        if websocket
        else (player.player_name if player else "Unknown")
    )
    status_payload = {
        "player_id": player_id,
        "player_name": player_name,
        "question_id": question_id,
        "strike_count": record.strike_count,
        "max_strikes": max_strikes,
        "is_frozen": True,
        "frozen_question_id": question_id,
        "is_kicked": record.is_kicked,
        "reason": reason,
        "fair_play_reason": reason,
        "answer_status": "frozen",
        "response_voided": response_voided,
    }
    manager.update_fair_play_status(
        session_code,
        player_id,
        strike_count=record.strike_count,
        max_strikes=max_strikes,
        is_frozen=True,
        frozen_question_id=question_id,
        is_kicked=record.is_kicked,
        reason=reason,
        fair_play_reason=reason,
        answer_status="frozen",
    )
    logger.info(
        "FAIR PLAY STRIKE session=%s player=%s question=%s strikes=%s/%s kicked=%s",
        session_code,
        player_id,
        question_id,
        record.strike_count,
        max_strikes,
        record.is_kicked,
    )

    await manager.broadcast_to_session(
        session_code,
        {
            "type": "player_flagged",
            "data": status_payload,
        },
        critical=True,
    )
    await manager.broadcast_to_session(
        session_code,
        {
            "type": "fair_play_status_update",
            "data": status_payload,
        },
        critical=True,
    )
    for connection_info in manager.get_player_connections(
        session_code, player_id
    ).values():
        player_websocket = connection_info.get("websocket")
        if player_websocket:
            await manager.send_personal_critical_message(
                session_code,
                {
                    "type": "fair_play_status_update",
                    "data": status_payload,
                },
                player_websocket,
            )
    await manager.broadcast_to_session(
        session_code,
        {
            "type": "player_answered",
            "data": {
                **status_payload,
                "answered_at": datetime.now().isoformat(),
                "answered_current": True,
            },
        },
        critical=True,
    )
    await apply_buzzer_fair_play_freeze(
        session_code=session_code,
        player_id=player_id,
        question_id=question_id,
        db=db,
    )

    if record.is_kicked:
        await kick_player_for_fair_play(session_code, player_id, max_strikes, db)
        await asyncio.sleep(0.75)
    await advance_after_fair_play_if_ready(session_code, question_id, db)


async def kick_player_for_fair_play(
    session_code: str,
    player_id: str,
    max_strikes: int,
    db: Optional[Session] = None,
):
    player_name = player_id
    if db:
        player = get_player_by_ID(db, player_id)
        if player and player.player_name:
            player_name = player.player_name

    message = f"You were removed after {max_strikes} Fair Play strikes."
    manager.update_fair_play_status(
        session_code,
        player_id,
        player_name=player_name,
        is_kicked=True,
        is_frozen=True,
        reason="fair_play_strikes",
        fair_play_reason="fair_play_strikes",
        strike_count=max_strikes,
        max_strikes=max_strikes,
        answer_status="kicked",
        message=message,
    )
    connections = manager.get_player_connections(session_code, player_id)
    for connection_info in connections.values():
        websocket = connection_info.get("websocket")
        if not websocket:
            continue

        await manager.send_personal_message(
            {
                "type": "fair_play_status_update",
                "data": {
                    "player_id": player_id,
                    "player_name": player_name,
                    "strike_count": max_strikes,
                    "max_strikes": max_strikes,
                    "is_kicked": True,
                    "is_frozen": True,
                    "reason": "fair_play_strikes",
                    "fair_play_reason": "fair_play_strikes",
                    "answer_status": "kicked",
                    "message": message,
                },
            },
            websocket,
        )
        await manager.send_personal_message(
            {
                "type": "kicked_from_session",
                "data": {
                    "player_id": player_id,
                    "player_name": player_name,
                    "reason": "fair_play_strikes",
                    "strike_count": max_strikes,
                    "max_strikes": max_strikes,
                    "is_kicked": True,
                    "message": message,
                },
            },
            websocket,
        )
        await asyncio.sleep(0.25)
        try:
            await websocket.close(
                code=4003,
                reason="Removed after Fair Play strikes",
            )
        except Exception as e:
            logger.debug(f"Error closing kicked player websocket: {e}")

    manager.disconnect_player_by_id(session_code, player_id)
    await manager.broadcast_to_session(
        session_code,
        {
            "type": "player_kicked",
            "data": {
                "player_id": player_id,
                "player_name": player_name,
                "reason": "fair_play_strikes",
                "strike_count": max_strikes,
                "max_strikes": max_strikes,
                "is_kicked": True,
            },
        },
        critical=True,
    )
    await manager.broadcast_player_roster_update(session_code)


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
    Handle legacy request to reveal the current question.

    This intentionally uses the synchronized countdown path instead of directly
    broadcasting question_started.
    """
    try:
        # Get current game state
        game_state = get_game_session_state(db, session_code)
        if not game_state:
            logger.error(f"Game session {session_code} not found")
            return

        if not game_state.current_question_id:
            logger.error(f"No current question for session {session_code}")
            return

        manager.clear_question_queue(session_code)
        await start_countdown(
            session_code,
            reason="broadcast_current_question",
            current_question_id=game_state.current_question_id,
            current_question_index=game_state.current_question_index,
            total_questions=game_state.total_questions,
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
        logger.info(f"ðŸŽ® Starting game for session {session_code}")

        # Emit an authoritative roster snapshot at game-start boundary.
        await manager.broadcast_player_roster_update(session_code)

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
            f"ðŸ“Š Roster validation - WebSocket: {ws_player_count} players, Database: {db_player_count} players"
        )

        # If counts don't match, broadcast roster update and wait briefly
        if ws_player_count != db_player_count:
            logger.warning(
                f"âš ï¸ Roster mismatch detected! Broadcasting roster update to sync..."
            )
            await manager.broadcast_player_roster_update(session_code)

            # Give frontend time to update (small delay)
            await asyncio.sleep(0.5)

            # Re-check after roster update
            db_player_count = get_number_of_players_in_session(db, session_code)
            logger.info(
                f"ðŸ“Š After roster sync - WebSocket: {ws_player_count}, Database: {db_player_count}"
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
                    f"ðŸ“ Including first question in game_started: {question_full['question_id']}, ui_mode={ui_mode}, game_type=trivia"
                )
            except Exception as e:
                logger.error(f"Error getting first question for game_started: {e}")

        # Step 1: Broadcast game_started event WITHOUT question data
        # This prevents mobiles from rendering early before intro completes
        logger.info(
            f"ðŸ“¡ Broadcasting game_started message for session {session_code}"
        )

        phase_state = manager.set_session_phase(
            session_code,
            SessionPhase.INTRO_AUDIO,
            current_question_id=(
                game_state.current_question_id if game_state else None
            ),
            current_question_index=(
                game_state.current_question_index if game_state else 0
            ),
            total_questions=(game_state.total_questions if game_state else 1),
        )

        game_started_data = {
            "session_code": session_code,
            "started_at": datetime.now().isoformat(),
            "isstarted": True,
            "phase": phase_state["phase"],
            "phase_started_at": phase_state["phase_started_at"],
            "phase_started_at_ms": phase_state["phase_started_at_ms"],
            "server_time_ms": phase_state["server_time_ms"],
            "fair_play_enabled": (
                game_state.fair_play_enabled if game_state else False
            ),
            "cheat_detection_enabled": (
                game_state.fair_play_enabled if game_state else False
            ),
            "max_fair_play_strikes": (
                game_state.max_fair_play_strikes if game_state else 3
            ),
            "max_cheat_strikes": (
                game_state.max_fair_play_strikes if game_state else 3
            ),
            "game_state": {
                "isstarted": True,
                "is_active": game_state.is_active if game_state else True,
                "current_question_index": (
                    game_state.current_question_index if game_state else 0
                ),
                "total_questions": (game_state.total_questions if game_state else 1),
                "fair_play_enabled": (
                    game_state.fair_play_enabled if game_state else False
                ),
                "max_fair_play_strikes": (
                    game_state.max_fair_play_strikes if game_state else 3
                ),
            },
        }

        # DO NOT include question in game_started - prevents early mobile rendering.
        # The server sends it from the countdown scheduler.

        await manager.broadcast_to_session(
            session_code,
            {
                "type": "game_started",
                "data": game_started_data,
            },
            critical=True,
            require_ack=True,
        )

        await manager.broadcast_to_session(
            session_code,
            {
                "type": "intro_started",
                "data": {
                    **phase_state,
                    "session_code": session_code,
                    "isstarted": True,
                },
            },
            critical=True,
        )

        # Re-emit roster right after game_started to keep leaderboard in sync
        # during page transition/reconnect windows.
        await manager.broadcast_player_roster_update(session_code)

        # Step 2: Send web-only preload so host can prepare UI during intro
        # This is NOT visible to mobiles - prevents the race condition
        if first_question_data:
            logger.info(
                f"ðŸ“º Sending preload_question to WEB only (not visible to mobile yet)"
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
            logger.info(f"âœ… Web host can now prepare question UI during intro")
        else:
            logger.warning("âš ï¸ No question data available for preload!")

        # Step 3: DO NOT broadcast question_started here.
        # The synchronized reveal happens from start_countdown().
        # This keeps intro, countdown, and question timing server-owned.
        logger.info(
            "Question broadcast deferred until the server-owned countdown completes"
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
        from app.logic.game_logic import get_game_session_state

        # Get current game state
        game_state = get_game_session_state(db, session_code)
        if not game_state or not game_state.current_question_id:
            logger.error(f"No current question found for session {session_code}")
            return

        await advance_or_end_current_question(session_code, db, reason="next_question")

    except Exception as e:
        logger.error(f"Error advancing to next question: {e}")


# REST endpoints for WebSocket management
@router.get("/ws/health")
async def websocket_health_check():
    """
    Health check endpoint for WebSocket infrastructure.
    Monitor this endpoint to detect API issues early.
    """
    import psutil
    import os

    # Get system memory info
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    memory_mb = memory_info.rss / 1024 / 1024

    # Get WebSocket stats
    total_connections = manager.get_total_connection_count()
    active_sessions = manager.get_active_session_count()

    # Calculate per-session average
    avg_connections_per_session = (
        total_connections / active_sessions if active_sessions > 0 else 0
    )

    # Determine health status
    status = "healthy"
    warnings = []

    # Check for warning conditions
    if total_connections > 100:
        warnings.append(f"High connection count: {total_connections} (threshold: 100)")
        status = "warning"

    if memory_mb > 500:
        warnings.append(f"High memory usage: {memory_mb:.1f}MB (threshold: 500MB)")
        status = "warning"

    if avg_connections_per_session > 10:
        warnings.append(
            f"High connections per session: {avg_connections_per_session:.1f} (possible duplicate bug)"
        )
        status = "critical"

    return {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "metrics": {
            "total_connections": total_connections,
            "active_sessions": active_sessions,
            "avg_connections_per_session": round(avg_connections_per_session, 2),
            "memory_usage_mb": round(memory_mb, 2),
        },
        "warnings": warnings,
    }


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
