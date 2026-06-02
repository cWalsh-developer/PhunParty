"""
WebSocket Connection Manager for PhunParty Game Sessions
Handles real-time communication between web UI and mobile app
"""

import asyncio
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class SessionPhase(str, Enum):
    LOBBY = "lobby"
    INTRO_AUDIO = "intro_audio"
    COUNTDOWN = "countdown"
    QUESTION = "question"
    ANSWER_REVEAL = "answer_reveal"
    RESULTS = "results"
    ENDED = "ended"


class ConnectionManager:
    """Manages WebSocket connections for game sessions"""

    HEARTBEAT_CHECK_INTERVAL_SECONDS = 10
    HEARTBEAT_STALE_SECONDS = 90
    MOBILE_HEARTBEAT_STALE_SECONDS = 300
    HEARTBEAT_UNSTABLE_SECONDS = 20
    HEARTBEAT_DISCONNECTED_SECONDS = 60
    PING_INTERVAL_SECONDS = 10
    MOBILE_DISCONNECT_GRACE_SECONDS = 30
    ACK_RETRY_DELAY_SECONDS = 1.5
    ACK_MAX_RESENDS = 2
    ACK_EVENT_TYPES = {
        "game_started",
        "countdown_started",
        "question_started",
        "game_ended",
    }

    def __init__(self):
        # session_code -> {websocket_id: {websocket, client_type, player_info}}
        self.active_connections: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # websocket_id -> {session_code, websocket}
        self.websocket_registry: Dict[str, Dict[str, Any]] = {}
        # Question queue: session_code -> {question_id: question_data}
        # Stores questions that have been broadcast so mobile clients can retrieve them
        self.question_queue: Dict[str, Dict[str, Any]] = {}
        # session_code -> authoritative phase/timing snapshot.
        self.session_phase_state: Dict[str, Dict[str, Any]] = {}
        # session_code -> shared buzzer state. Handlers are per-connection, so
        # buzzer state must live at session scope.
        self.buzzer_states: Dict[str, Dict[str, Any]] = {}
        # session_code -> resolved game mode, shared across scheduler/handlers.
        self.session_game_types: Dict[str, str] = {}
        # session_code -> player_id -> frozen question id for Fair Play violations.
        self.fair_play_frozen_players: Dict[str, Dict[str, str]] = {}
        # session_code -> player_id -> Fair Play UI state included in roster payloads.
        self.fair_play_player_status: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # event_id -> delivery/ack state for critical phase messages.
        self.pending_acks: Dict[str, Dict[str, Any]] = {}
        # session_code:player_id values for players who explicitly left.
        self.intentional_leaves: Set[str] = set()
        # player leave tasks: "session_code:player_id" -> asyncio.Task
        # Used to avoid flapping presence when mobile networks briefly disconnect.
        self.pending_player_leave_tasks: Dict[str, asyncio.Task] = {}
        # Start heartbeat checker and automatic ping broadcaster
        self._heartbeat_task = None
        self._ping_task = None
        self._start_heartbeat_checker()
        self._start_automatic_ping()

    def _player_task_key(self, session_code: str, player_id: str) -> str:
        return f"{session_code}:{player_id}"

    def _cancel_pending_player_leave(self, session_code: str, player_id: Optional[str]):
        if not player_id:
            return

        task_key = self._player_task_key(session_code, player_id)
        existing_task = self.pending_player_leave_tasks.get(task_key)
        if existing_task and not existing_task.done():
            existing_task.cancel()
            logger.info(
                f"♻️ Cancelled pending leave for player {player_id} in session {session_code}"
            )
        self.pending_player_leave_tasks.pop(task_key, None)

    def _is_player_leave_pending(
        self, session_code: str, player_id: Optional[str]
    ) -> bool:
        if not player_id:
            return False

        task_key = self._player_task_key(session_code, player_id)
        task = self.pending_player_leave_tasks.get(task_key)
        return bool(task and not task.done())

    def _schedule_mobile_leave(self, session_code: str, client_info: Dict[str, Any]):
        player_id = client_info.get("player_id")
        player_name = client_info.get("player_name") or "Unknown"

        if not player_id:
            return

        self._cancel_pending_player_leave(session_code, player_id)
        task_key = self._player_task_key(session_code, player_id)

        async def delayed_leave_broadcast():
            try:
                await asyncio.sleep(self.MOBILE_DISCONNECT_GRACE_SECONDS)

                # If player reconnected during grace window, do not broadcast leave.
                if self.get_player_connections(session_code, player_id):
                    logger.info(
                        f"✅ Player {player_name} reconnected within grace window in session {session_code}"
                    )
                    return

                logger.info(
                    f"📴 Player {player_name} did not reconnect after {self.MOBILE_DISCONNECT_GRACE_SECONDS}s grace period"
                )

                await self.broadcast_to_session(
                    session_code,
                    {
                        "type": "player_left",
                        "data": {
                            "player_id": player_id,
                            "player_name": player_name,
                            "timestamp": datetime.now().isoformat(),
                        },
                    },
                    exclude_client_types=["mobile"],
                    critical=True,
                )

                # Keep all clients in sync after confirmed leave.
                await self.broadcast_player_roster_update(session_code)

            except asyncio.CancelledError:
                logger.debug(
                    f"Pending leave task cancelled for {player_name} in session {session_code}"
                )
            except Exception as e:
                logger.error(
                    f"Error during delayed leave broadcast for {player_name}: {e}"
                )
            finally:
                self.pending_player_leave_tasks.pop(task_key, None)

        self.pending_player_leave_tasks[task_key] = asyncio.create_task(
            delayed_leave_broadcast()
        )

    def generate_websocket_id(self, websocket: WebSocket) -> str:
        """Generate unique ID for WebSocket connection"""
        return f"ws_{id(websocket)}_{datetime.now().timestamp()}"

    def _utc_now_ms(self) -> int:
        return int(datetime.utcnow().timestamp() * 1000)

    def _utc_now_iso(self) -> str:
        return datetime.utcnow().isoformat() + "Z"

    def make_event_id(
        self, session_code: str, event_type: str, data: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build a deterministic event id so clients can safely ignore duplicates."""
        data = data or {}
        question_id = data.get("question_id") or data.get("current_question_id")
        question_index = data.get("current_question_index")
        start_at = data.get("start_at") or data.get("question_start_at")
        phase_started_at_ms = data.get("phase_started_at_ms")

        parts = [session_code, event_type]
        if question_id:
            parts.append(str(question_id))
        elif question_index is not None:
            parts.append(f"q{question_index}")

        if start_at:
            parts.append(str(start_at))
        elif phase_started_at_ms:
            parts.append(str(phase_started_at_ms))

        return ":".join(parts)

    def set_session_phase(
        self, session_code: str, phase: Union[SessionPhase, str], **updates: Any
    ) -> Dict[str, Any]:
        """Update the authoritative in-memory phase snapshot for a session."""
        phase_value = phase.value if isinstance(phase, SessionPhase) else str(phase)
        clear_fields = updates.pop("clear_fields", None) or []
        now_iso = self._utc_now_iso()
        now_ms = self._utc_now_ms()

        state = self.session_phase_state.setdefault(
            session_code,
            {
                "session_code": session_code,
                "phase": SessionPhase.LOBBY.value,
                "phase_started_at": now_iso,
                "phase_started_at_ms": now_ms,
            },
        )

        state.update(
            {
                "session_code": session_code,
                "phase": phase_value,
                "phase_started_at": now_iso,
                "phase_started_at_ms": now_ms,
                "server_time_ms": now_ms,
                "updated_at": now_iso,
            }
        )
        for key in clear_fields:
            state.pop(key, None)
        state.update({key: value for key, value in updates.items() if value is not None})
        logger.info(f"Session {session_code} phase set to {phase_value}")
        return dict(state)

    def get_session_phase_state(self, session_code: str) -> Dict[str, Any]:
        """Return the authoritative phase snapshot, defaulting to lobby."""
        state = self.session_phase_state.get(session_code)
        if state:
            return {**state, "server_time_ms": self._utc_now_ms()}

        now_iso = self._utc_now_iso()
        now_ms = self._utc_now_ms()
        return {
            "session_code": session_code,
            "phase": SessionPhase.LOBBY.value,
            "phase_started_at": now_iso,
            "phase_started_at_ms": now_ms,
            "server_time_ms": now_ms,
        }

    def get_session_sync_state(self, session_code: str) -> Dict[str, Any]:
        """Build a reconnect-safe snapshot from server-owned WebSocket state."""
        phase_state = self.get_session_phase_state(session_code)
        current_question = self.get_current_question(session_code)
        return {
            **phase_state,
            "connected_players": self.get_mobile_players(session_code),
            "connection_stats": self.get_session_stats(session_code),
            "current_question": current_question,
        }

    def acknowledge_event(self, websocket: WebSocket, event_id: str) -> bool:
        """Mark a critical event as acknowledged by this websocket."""
        ws_id = None
        for registry_ws_id, info in self.websocket_registry.items():
            if info["websocket"] == websocket:
                ws_id = registry_ws_id
                break

        if not ws_id:
            logger.warning(f"ACK received for {event_id} from unknown websocket")
            return False

        event_state = self.pending_acks.get(event_id)
        if not event_state:
            logger.debug(f"ACK received for unknown or completed event {event_id}")
            return False

        target_state = event_state["targets"].get(ws_id)
        if not target_state:
            logger.debug(f"ACK received for {event_id} from non-target websocket {ws_id}")
            return False

        target_state["acked"] = True
        target_state["acked_at"] = self._utc_now_iso()
        logger.debug(f"ACK received for {event_id} from {ws_id}")

        if all(target.get("acked") for target in event_state["targets"].values()):
            logger.debug(f"All targets acknowledged {event_id}")
            self.pending_acks.pop(event_id, None)

        return True

    def get_pending_ack_summary(self, session_code: Optional[str] = None) -> Dict[str, Any]:
        events = [
            event
            for event in self.pending_acks.values()
            if session_code is None or event.get("session_code") == session_code
        ]
        pending_targets = 0
        for event in events:
            pending_targets += sum(
                1 for target in event["targets"].values() if not target.get("acked")
            )

        return {
            "events": len(events),
            "pending_targets": pending_targets,
        }

    def _track_ack_target(
        self,
        event_id: str,
        session_code: str,
        message: Dict[str, Any],
        ws_id: str,
        connection_info: Dict[str, Any],
    ) -> None:
        event_state = self.pending_acks.setdefault(
            event_id,
            {
                "event_id": event_id,
                "session_code": session_code,
                "message": message,
                "created_at": self._utc_now_iso(),
                "resend_count": 0,
                "targets": {},
            },
        )
        event_state["targets"][ws_id] = {
            "acked": False,
            "client_type": connection_info.get("client_type"),
            "player_id": connection_info.get("player_id"),
            "player_name": connection_info.get("player_name"),
            "sent_at": self._utc_now_iso(),
        }

    def _schedule_ack_retry(self, event_id: str) -> None:
        try:
            asyncio.create_task(self._retry_unacked_event(event_id))
        except RuntimeError:
            logger.debug(f"Could not schedule ACK retry for {event_id}; no event loop")

    async def _retry_unacked_event(self, event_id: str) -> None:
        while event_id in self.pending_acks:
            await asyncio.sleep(self.ACK_RETRY_DELAY_SECONDS)
            event_state = self.pending_acks.get(event_id)
            if not event_state:
                return

            if all(target.get("acked") for target in event_state["targets"].values()):
                self.pending_acks.pop(event_id, None)
                return

            resend_count = event_state.get("resend_count", 0)
            if resend_count >= self.ACK_MAX_RESENDS:
                missing = [
                    ws_id
                    for ws_id, target in event_state["targets"].items()
                    if not target.get("acked")
                ]
                logger.warning(
                    f"ACK timeout for {event_id}; missing {len(missing)} target(s): {missing}"
                )
                self.pending_acks.pop(event_id, None)
                return

            session_code = event_state["session_code"]
            message = {
                **event_state["message"],
                "retry_count": resend_count + 1,
            }

            for ws_id, target in list(event_state["targets"].items()):
                if target.get("acked"):
                    continue

                connection_info = self.active_connections.get(session_code, {}).get(ws_id)
                if not connection_info:
                    event_state["targets"].pop(ws_id, None)
                    continue

                sent = await self.send_personal_message(
                    message,
                    connection_info["websocket"],
                    retries=0,
                )
                if sent:
                    target["resent_at"] = self._utc_now_iso()

            event_state["resend_count"] = resend_count + 1

    async def connect(
        self,
        websocket: WebSocket,
        session_code: str,
        client_type: str = "web",  # "web" or "mobile"
        player_id: Optional[str] = None,
        player_name: Optional[str] = None,
        player_photo: Optional[str] = None,
    ):
        """Connect a client to a game session"""
        await websocket.accept()

        if client_type == "mobile" and not player_name:
            player_name = player_id or "Unknown player"

        reconnecting_mobile_player = False
        if client_type == "mobile" and player_id:
            self.intentional_leaves.discard(
                self._player_task_key(session_code, player_id)
            )
            reconnecting_mobile_player = self._is_player_leave_pending(
                session_code, player_id
            )

        ws_id = self.generate_websocket_id(websocket)

        # Initialize session if it doesn't exist
        if session_code not in self.active_connections:
            self.active_connections[session_code] = {}

        # Store connection info with ready flag
        connection_info = {
            "websocket": websocket,
            "client_type": client_type,
            "connected_at": datetime.now().isoformat(),
            "player_id": player_id,
            "player_name": player_name,
            "player_photo": player_photo,
            "player_answered": False,
            "connection_state": "connected",
            "last_heartbeat": datetime.now(),
            "ws_id": ws_id,
            "is_ready": False,  # Track if client acknowledged connection
            "connection_confirmed": False,
        }

        self.active_connections[session_code][ws_id] = connection_info
        self.websocket_registry[ws_id] = {
            "session_code": session_code,
            "websocket": websocket,
        }

        logger.info(
            f"Client connected: {client_type} to session {session_code} (ws_id: {ws_id}, player: {player_name or 'N/A'})"
        )
        logger.info(
            "CONNECT DEBUG session=%s client_type=%s player_id=%s player_name=%s player_photo=%s",
            session_code,
            client_type,
            player_id,
            player_name,
            player_photo,
        )

        # Send connection confirmation to the connecting client and wait for ack
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "connection_established",
                        "data": {
                            "ws_id": ws_id,
                            "session_code": session_code,
                            "client_type": client_type,
                            "player_id": player_id,
                            "player_name": player_name,
                            "timestamp": datetime.now().isoformat(),
                            "requires_ack": True,
                        },
                        "timestamp": datetime.now().timestamp(),
                    }
                )
            )

            # Mark connection as confirmed after successful send
            connection_info["connection_confirmed"] = True
            logger.info(
                f"Connection confirmation sent to {client_type} client (ws_id: {ws_id})"
            )

        except Exception as e:
            logger.error(f"Failed to send connection confirmation: {e}")
            # Clean up partially registered connection to avoid ghost players.
            self.disconnect(websocket)
            return

        # If this is a reconnect, cancel pending delayed leave for this player.
        if client_type == "mobile" and player_id:
            self._cancel_pending_player_leave(session_code, player_id)

        # Notify other clients about new connection IMMEDIATELY (if mobile player joining)
        # REMOVED: await asyncio.sleep(0.2) - This delay was causing web UI to miss player joins
        if client_type == "mobile":
            logger.info(
                f"📢 Mobile player {player_name} connected - broadcasting IMMEDIATELY to session {session_code}"
            )

            # Get current player count BEFORE broadcasting
            mobile_count = len(
                [
                    c
                    for c in self.active_connections[session_code].values()
                    if c.get("client_type") == "mobile"
                    and c.get("connection_confirmed")
                ]
            )

            logger.info(f"📊 Current mobile player count: {mobile_count}")

            # Only emit player_joined for true fresh joins.
            # Reconnects within grace should keep presence stable without join/leave flicker.
            if not reconnecting_mobile_player:
                await self.broadcast_to_session(
                    session_code,
                    {
                        "type": "player_joined",
                        "data": {
                            "player_id": player_id,
                            "player_name": player_name,
                            "player_photo": player_photo,
                            "timestamp": datetime.now().isoformat(),
                            "total_players": mobile_count,
                        },
                    },
                    exclude_client_types=["mobile"],  # Only notify web clients
                    critical=True,  # Mark as critical for retry logic
                )
                logger.info(f"✅ Sent player_joined event for {player_name}")
            else:
                logger.info(
                    f"🔁 Player {player_name} reconnected within grace window; skipping duplicate player_joined"
                )

            # CRITICAL: Send roster update to ALL clients (web + mobile)
            # This ensures everyone has the latest player list
            await self.broadcast_player_roster_update(session_code)

            logger.info(
                f"✅ Sent roster_update to all clients in session {session_code}"
            )

            logger.info(
                f"✅ Mobile join flow completed for {player_name} in session {session_code}"
            )

    def disconnect(self, websocket: WebSocket):
        """Disconnect a client"""
        ws_id = None
        session_code = None
        client_info = None

        # Find the websocket in registry
        for ws_id, info in self.websocket_registry.items():
            if info["websocket"] == websocket:
                session_code = info["session_code"]
                client_info = self.active_connections[session_code][ws_id]
                break

        if ws_id and session_code:
            # Remove from connections
            if session_code in self.active_connections:
                if ws_id in self.active_connections[session_code]:
                    del self.active_connections[session_code][ws_id]

                # Clean up empty session
                if not self.active_connections[session_code]:
                    del self.active_connections[session_code]

            # Remove from registry
            if ws_id in self.websocket_registry:
                del self.websocket_registry[ws_id]

            logger.info(f"Client disconnected from session {session_code}")

            # For mobile clients, delay leave notification to tolerate brief reconnect gaps.
            if (
                client_info
                and client_info.get("client_type") == "mobile"
                and client_info.get("player_id")
                and self._player_task_key(
                    session_code, client_info.get("player_id")
                )
                not in self.intentional_leaves
            ):
                self._schedule_mobile_leave(session_code, client_info)

    def cleanup_session(self, session_code: str) -> None:
        """Drop in-memory state for a completed session once clients have left."""
        active_connections = self.active_connections.get(session_code, {})
        if active_connections:
            logger.debug(
                f"Skipping active connection cleanup for session {session_code}; {len(active_connections)} connection(s) remain"
            )
        else:
            self.active_connections.pop(session_code, None)

        self.question_queue.pop(session_code, None)
        self.session_phase_state.pop(session_code, None)
        self.buzzer_states.pop(session_code, None)
        self.session_game_types.pop(session_code, None)
        self.fair_play_frozen_players.pop(session_code, None)
        self.fair_play_player_status.pop(session_code, None)

        session_key_prefix = f"{session_code}:"
        for task_key, task in list(self.pending_player_leave_tasks.items()):
            if task_key.startswith(session_key_prefix):
                if task and not task.done():
                    task.cancel()
                self.pending_player_leave_tasks.pop(task_key, None)

        self.intentional_leaves = {
            key
            for key in self.intentional_leaves
            if not key.startswith(session_key_prefix)
        }

        for event_id, event_state in list(self.pending_acks.items()):
            if event_state.get("session_code") == session_code:
                self.pending_acks.pop(event_id, None)

        logger.info(f"Cleaned in-memory websocket state for session {session_code}")

    async def cleanup_session_later(
        self, session_code: str, delay_seconds: int = 60
    ) -> None:
        await asyncio.sleep(delay_seconds)
        self.cleanup_session(session_code)

    async def send_personal_message(
        self, message: dict, websocket: WebSocket, retries: int = 2
    ):
        """Send message to specific WebSocket with retry logic"""
        for attempt in range(retries + 1):
            try:
                await websocket.send_text(
                    json.dumps({**message, "timestamp": datetime.now().timestamp()})
                )
                return True
            except WebSocketDisconnect:
                logger.warning(
                    f"WebSocket disconnected during send (attempt {attempt + 1}/{retries + 1})"
                )
                if attempt == retries:
                    return False
            except Exception as e:
                logger.error(
                    f"Error sending personal message (attempt {attempt + 1}/{retries + 1}): {e}"
                )
                if attempt == retries:
                    return False
                await asyncio.sleep(0.1 * (attempt + 1))  # Exponential backoff
        return False

    async def send_personal_message_by_id(self, message: dict, websocket_id: str):
        """Send message to specific WebSocket by ID"""
        try:
            if websocket_id in self.websocket_registry:
                websocket = self.websocket_registry[websocket_id]["websocket"]
                await self.send_personal_message(message, websocket)
            else:
                logger.warning(f"WebSocket ID {websocket_id} not found in registry")
        except Exception as e:
            logger.error(f"Error sending personal message by ID: {e}")

    async def send_personal_critical_message(
        self, session_code: str, message: dict, websocket: WebSocket
    ) -> bool:
        """Send one critical event with normal event_id/ACK tracking metadata."""
        data = message.get("data", {})
        message_id = message.get("message_id") or self.make_event_id(
            session_code,
            message.get("type", "event"),
            data if isinstance(data, dict) else {},
        )
        message_with_metadata = {
            **message,
            "message_id": message_id,
            "event_id": message.get("event_id") or message_id,
            "requires_ack": True,
        }

        sent = await self.send_personal_message(message_with_metadata, websocket)
        if not sent:
            return False

        for ws_id, registry_info in self.websocket_registry.items():
            if registry_info["websocket"] != websocket:
                continue

            connection_info = self.active_connections.get(session_code, {}).get(ws_id)
            if not connection_info:
                return sent

            self._track_ack_target(
                message_with_metadata["event_id"],
                session_code,
                message_with_metadata,
                ws_id,
                connection_info,
            )
            self._schedule_ack_retry(message_with_metadata["event_id"])
            break

        return sent

    async def broadcast_to_session(
        self,
        session_code: str,
        message: dict,
        exclude_websockets: Optional[List[WebSocket]] = None,
        only_client_types: Optional[List[str]] = None,
        exclude_client_types: Optional[List[str]] = None,
        critical: bool = False,
        require_ack: bool = False,
    ):
        """Broadcast message to all clients in a session with filtering options and reliability"""
        if session_code not in self.active_connections:
            logger.warning(
                f"Cannot broadcast to session {session_code} - no active connections"
            )
            return

        exclude_websockets = exclude_websockets or []
        message_with_timestamp = {**message, "timestamp": datetime.now().timestamp()}

        # Add message ID for tracking
        data = message.get("data", {})
        message_id = message.get("message_id") or self.make_event_id(
            session_code,
            message.get("type", "event"),
            data if isinstance(data, dict) else {},
        )
        message_with_timestamp["message_id"] = message_id
        should_require_ack = require_ack or message.get("type") in self.ACK_EVENT_TYPES
        if should_require_ack:
            message_with_timestamp["event_id"] = message.get("event_id") or message_id
            message_with_timestamp["requires_ack"] = True

        disconnected_websockets = []
        success_count = 0
        total_targets = 0
        mobile_sent = 0
        web_sent = 0

        filter_info = ""
        if only_client_types:
            filter_info = f" (only: {', '.join(only_client_types)})"
        elif exclude_client_types:
            filter_info = f" (exclude: {', '.join(exclude_client_types)})"

        logger.debug(
            f"📡 Broadcasting '{message.get('type')}' to session {session_code}{filter_info}"
        )

        for ws_id, connection_info in list(
            self.active_connections[session_code].items()
        ):
            websocket = connection_info["websocket"]
            client_type = connection_info["client_type"]
            player_name = connection_info.get("player_name", "N/A")

            # Skip excluded websockets
            if websocket in exclude_websockets:
                continue

            # Filter by client type if specified
            if only_client_types and client_type not in only_client_types:
                logger.debug(
                    f"  ⊘ Skipping {client_type} client {ws_id} (filtered out)"
                )
                continue

            if exclude_client_types and client_type in exclude_client_types:
                logger.debug(f"  ⊘ Skipping {client_type} client {ws_id} (excluded)")
                continue

            total_targets += 1
            logger.debug(
                f"  → Sending to {client_type} client {ws_id} (player: {player_name})"
            )

            # Retry logic for critical messages
            max_attempts = 3 if critical else 1
            sent = False

            for attempt in range(max_attempts):
                try:
                    await websocket.send_text(json.dumps(message_with_timestamp))
                    if should_require_ack:
                        self._track_ack_target(
                            message_with_timestamp["event_id"],
                            session_code,
                            message_with_timestamp,
                            ws_id,
                            connection_info,
                        )
                    success_count += 1
                    if client_type == "mobile":
                        mobile_sent += 1
                    elif client_type == "web":
                        web_sent += 1
                    sent = True
                    logger.debug(f"  ✓ Sent successfully to {client_type} {ws_id}")
                    break
                except WebSocketDisconnect:
                    logger.warning(
                        f"WebSocket {ws_id} ({client_type}) disconnected during broadcast"
                    )
                    disconnected_websockets.append(websocket)
                    break
                except Exception as e:
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Retry {attempt + 1}/{max_attempts} for {ws_id}: {e}"
                        )
                        await asyncio.sleep(0.05)
                    else:
                        logger.error(
                            f"Failed to send to {ws_id} after {max_attempts} attempts: {e}"
                        )
                        disconnected_websockets.append(websocket)

        logger.debug(
            f"✅ Broadcast complete: {success_count}/{total_targets} clients received '{message.get('type')}' (📱mobile: {mobile_sent}, 💻web: {web_sent})"
        )

        # Clean up disconnected websockets
        for ws in disconnected_websockets:
            self.disconnect(ws)

        if should_require_ack and success_count > 0:
            self._schedule_ack_retry(message_with_timestamp["event_id"])

    async def broadcast_to_mobile_players(self, session_code: str, message: dict):
        """Broadcast message only to mobile clients"""
        session_connections = self.active_connections.get(session_code, {})
        mobile_connections = [
            conn
            for conn in session_connections.values()
            if conn["client_type"] == "mobile"
        ]
        mobile_count = len(mobile_connections)

        logger.debug(
            f"📱 Broadcasting to {mobile_count} mobile client(s) in session {session_code}: type={message.get('type')}"
        )

        if mobile_count == 0:
            logger.warning(f"⚠️ NO MOBILE CLIENTS connected to session {session_code}!")
        else:
            # Log details about connected mobile clients
            for conn in mobile_connections:
                logger.debug(
                    f"📱 Mobile client: player_id={conn.get('player_id')}, ws_id={conn.get('ws_id')}"
                )

        await self.broadcast_to_session(
            session_code, message, only_client_types=["mobile"], critical=True
        )

    async def broadcast_to_web_clients(self, session_code: str, message: dict):
        """Broadcast message only to web clients"""
        web_count = sum(
            1
            for conn in self.active_connections.get(session_code, {}).values()
            if conn["client_type"] == "web"
        )
        logger.debug(
            f"💻 Broadcasting to {web_count} web client(s) in session {session_code}: type={message.get('type')}"
        )
        await self.broadcast_to_session(
            session_code, message, only_client_types=["web"], critical=True
        )

    def get_session_connections(self, session_code: str) -> Dict[str, Dict[str, Any]]:
        """Get all connections for a session"""
        return self.active_connections.get(session_code, {})

    def get_mobile_players(self, session_code: str) -> List[Dict[str, Any]]:
        """Get list of mobile players in session"""
        connections = self.get_session_connections(session_code)
        latest_by_player: Dict[str, Dict[str, Any]] = {}
        unnamed_mobile_players: List[Dict[str, Any]] = []

        for connection_info in connections.values():
            if connection_info.get("client_type") != "mobile":
                continue

            player_id = connection_info.get("player_id")
            player_name = (
                connection_info.get("player_name") or player_id or "Unknown player"
            )

            player_data = {
                "player_id": player_id,
                "player_name": player_name,
                "player_photo": connection_info.get("player_photo"),
                "connected_at": connection_info.get("connected_at"),
                "player_answered": connection_info.get("player_answered", None),
                "connection_state": connection_info.get(
                    "connection_state", "connected"
                ),
            }
            if player_id:
                player_data.update(
                    self.fair_play_player_status.get(session_code, {}).get(
                        player_id, {}
                    )
                )

            if player_id:
                existing = latest_by_player.get(player_id)
                existing_connected_at = existing.get("connected_at") if existing else ""
                candidate_connected_at = player_data.get("connected_at") or ""
                if not existing or candidate_connected_at >= existing_connected_at:
                    latest_by_player[player_id] = player_data
            else:
                unnamed_mobile_players.append(player_data)

        # Return deterministic ordering so roster updates are stable.
        deduped_players = list(latest_by_player.values()) + unnamed_mobile_players
        deduped_players.sort(
            key=lambda p: (p.get("player_name") or "", p.get("connected_at") or "")
        )
        logger.info(
            "ROSTER DEBUG session=%s players=%s",
            session_code,
            [
                (player.get("player_name"), player.get("player_id"))
                for player in deduped_players
            ],
        )
        return deduped_players

    def get_session_stats(self, session_code: str) -> Dict[str, Any]:
        """Get statistics for a session"""
        connections = self.get_session_connections(session_code)
        web_clients = sum(
            1 for conn in connections.values() if conn["client_type"] == "web"
        )
        mobile_clients = sum(
            1 for conn in connections.values() if conn["client_type"] == "mobile"
        )

        return {
            "total_connections": len(connections),
            "web_clients": web_clients,
            "mobile_clients": mobile_clients,
            "mobile_players": self.get_mobile_players(session_code),
            "phase": self.get_session_phase_state(session_code).get("phase"),
            "pending_acks": self.get_pending_ack_summary(session_code),
        }

    async def send_personal_message_by_id(self, message: dict, websocket_id: str):
        """Send message to specific WebSocket by websocket_id"""
        try:
            # Find the websocket object by ID
            if websocket_id in self.websocket_registry:
                websocket = self.websocket_registry[websocket_id]["websocket"]
                await websocket.send_text(
                    json.dumps({**message, "timestamp": datetime.now().timestamp()})
                )
            else:
                logger.warning(f"WebSocket ID {websocket_id} not found in registry")
        except Exception as e:
            logger.error(f"Error sending personal message by ID: {e}")

    def get_websocket_by_id(self, websocket_id: str) -> Optional[WebSocket]:
        """Get WebSocket object by websocket_id"""
        if websocket_id in self.websocket_registry:
            return self.websocket_registry[websocket_id]["websocket"]
        return None

    def get_player_name_from_websocket(self, websocket: WebSocket) -> str:
        """Get player name from websocket for logging purposes"""
        for ws_id, info in self.websocket_registry.items():
            if info["websocket"] == websocket:
                session_code = info["session_code"]
                if (
                    session_code in self.active_connections
                    and ws_id in self.active_connections[session_code]
                ):
                    conn_info = self.active_connections[session_code][ws_id]
                    return conn_info.get("player_name") or "Unknown"
        return "Unknown"

    def get_player_connections(
        self, session_code: str, player_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get all active connections for a specific player in a session.
        Returns dict of {ws_id: connection_info}
        """
        if session_code not in self.active_connections:
            return {}

        player_connections = {}
        for ws_id, conn_info in self.active_connections[session_code].items():
            if (
                conn_info.get("client_type") == "mobile"
                and conn_info.get("player_id") == player_id
            ):
                player_connections[ws_id] = conn_info

        return player_connections

    def disconnect_player_by_id(self, session_code: str, player_id: str) -> int:
        """
        Disconnect all connections for a specific player.
        Returns number of connections disconnected.
        """
        if session_code not in self.active_connections:
            return 0

        disconnected_count = 0
        ws_ids_to_remove = []

        # Find all connections for this player
        for ws_id, conn_info in self.active_connections[session_code].items():
            if (
                conn_info.get("client_type") == "mobile"
                and conn_info.get("player_id") == player_id
            ):
                ws_ids_to_remove.append(ws_id)

        # Remove them
        for ws_id in ws_ids_to_remove:
            # Remove from session connections
            if ws_id in self.active_connections[session_code]:
                del self.active_connections[session_code][ws_id]
                disconnected_count += 1

            # Remove from registry
            if ws_id in self.websocket_registry:
                del self.websocket_registry[ws_id]

        logger.info(
            f"Disconnected {disconnected_count} connection(s) for player {player_id} in session {session_code}"
        )

        return disconnected_count

    def get_total_connection_count(self) -> int:
        """Get total number of active WebSocket connections across all sessions"""
        total = 0
        for session_connections in self.active_connections.values():
            total += len(session_connections)
        return total

    def get_active_session_count(self) -> int:
        """Get number of active game sessions"""
        return len(self.active_connections)

    def get_session_stats(self, session_code: str) -> Dict:
        """
        Get detailed statistics for a specific session.

        Args:
            session_code: Session code to get stats for

        Returns:
            Dictionary with connection statistics
        """
        if session_code not in self.active_connections:
            return {
                "exists": False,
                "total_connections": 0,
                "web_clients": 0,
                "mobile_clients": 0,
                "mobile_players": [],
                "players": 0,
                "hosts": 0,
                "observers": 0,
                "player_breakdown": {},
            }

        connections = self.active_connections[session_code]
        player_breakdown = {}
        web_clients = 0
        mobile_clients = 0
        hosts = 0
        observers = 0

        for ws_id, conn_info in connections.items():
            client_type = conn_info.get("client_type", "unknown")
            player_id = conn_info.get("player_id")

            if client_type == "host":
                hosts += 1
            elif client_type == "observer":
                observers += 1
            elif client_type == "web":
                web_clients += 1
            elif client_type == "mobile":
                mobile_clients += 1
            elif player_id:
                # Track connections per player
                if player_id not in player_breakdown:
                    player_breakdown[player_id] = {
                        "connection_count": 0,
                        "player_name": conn_info.get("player_name", "Unknown"),
                    }
                player_breakdown[player_id]["connection_count"] += 1

        return {
            "exists": True,
            "total_connections": len(connections),
            "web_clients": web_clients,
            "mobile_clients": mobile_clients,
            "mobile_players": self.get_mobile_players(session_code),
            "phase": self.get_session_phase_state(session_code).get("phase"),
            "pending_acks": self.get_pending_ack_summary(session_code),
            "players": len(player_breakdown),
            "hosts": hosts,
            "observers": observers,
            "player_breakdown": player_breakdown,
            "duplicate_connections": [
                {
                    "player_id": pid,
                    "player_name": info["player_name"],
                    "connection_count": info["connection_count"],
                }
                for pid, info in player_breakdown.items()
                if info["connection_count"] > 1
            ],
        }

    def set_player_answered(
        self, session_code: str, player_id: str, answered: bool = True
    ):
        """Set the answered status for a specific player in a session"""
        if session_code not in self.active_connections:
            logger.warning(
                f"Session {session_code} not found when setting player_answered"
            )
            return False

        for connection_info in self.active_connections[session_code].values():
            if (
                connection_info.get("player_id") == player_id
                and connection_info.get("client_type") == "mobile"
            ):
                connection_info["player_answered"] = answered
                logger.debug(
                    f"Set player_answered={answered} for player {player_id} in session {session_code}"
                )
                return True

        logger.warning(
            f"Player {player_id} not found in session {session_code} connections"
        )
        return False

    def reset_all_players_answered(self, session_code: str):
        """Reset the answered status for all players in a session (e.g., when new question starts)"""
        if session_code not in self.active_connections:
            logger.warning(
                f"Session {session_code} not found when resetting player_answered"
            )
            return

        count = 0
        for connection_info in self.active_connections[session_code].values():
            if connection_info.get("client_type") == "mobile":
                connection_info["player_answered"] = False
                count += 1

        logger.debug(
            f"Reset player_answered for {count} players in session {session_code}"
        )

    def get_player_answered_status(self, session_code: str, player_id: str) -> bool:
        """Get the answered status for a specific player"""
        if session_code not in self.active_connections:
            return False

        for connection_info in self.active_connections[session_code].values():
            if (
                connection_info.get("player_id") == player_id
                and connection_info.get("client_type") == "mobile"
            ):
                return connection_info.get("player_answered", False)

        return False

    def get_answered_count(self, session_code: str) -> int:
        """Get the count of players who have answered in a session"""
        if session_code not in self.active_connections:
            return 0

        return sum(
            1
            for connection_info in self.active_connections[session_code].values()
            if connection_info.get("client_type") == "mobile"
            and connection_info.get("player_answered", False)
        )

    def freeze_player_for_question(
        self, session_code: str, player_id: str, question_id: str
    ) -> None:
        self.fair_play_frozen_players.setdefault(session_code, {})[
            player_id
        ] = question_id
        self.update_fair_play_status(
            session_code,
            player_id,
            is_frozen=True,
            frozen_question_id=question_id,
        )

    def is_player_frozen_for_question(
        self, session_code: str, player_id: str, question_id: str
    ) -> bool:
        return (
            self.fair_play_frozen_players.get(session_code, {}).get(player_id)
            == question_id
        )

    def update_fair_play_status(
        self, session_code: str, player_id: str, **status: Any
    ) -> Dict[str, Any]:
        """Store host-visible Fair Play state for roster updates."""
        session_status = self.fair_play_player_status.setdefault(session_code, {})
        player_status = session_status.setdefault(player_id, {})
        player_status.update(status)
        return player_status

    def get_fair_play_status(
        self, session_code: str, player_id: str
    ) -> Dict[str, Any]:
        return dict(
            self.fair_play_player_status.get(session_code, {}).get(player_id, {})
        )

    def reset_fair_play_freezes_for_question(
        self, session_code: str, question_id: str
    ) -> None:
        frozen_players = self.fair_play_frozen_players.get(session_code)
        if not frozen_players:
            return

        for player_id, frozen_question_id in list(frozen_players.items()):
            if frozen_question_id != question_id:
                frozen_players.pop(player_id, None)
                self.update_fair_play_status(
                    session_code,
                    player_id,
                    is_frozen=False,
                    frozen_question_id=None,
                    answer_status=None,
                )

        if not frozen_players:
            self.fair_play_frozen_players.pop(session_code, None)

    def update_heartbeat(self, websocket: WebSocket):
        """Update the last heartbeat time for a websocket"""
        for ws_id, info in self.websocket_registry.items():
            if info["websocket"] == websocket:
                session_code = info["session_code"]
                if (
                    session_code in self.active_connections
                    and ws_id in self.active_connections[session_code]
                ):
                    self.active_connections[session_code][ws_id][
                        "last_heartbeat"
                    ] = datetime.now()
                    self.active_connections[session_code][ws_id][
                        "connection_state"
                    ] = "connected"
                break

    def mark_client_ready(self, websocket: WebSocket):
        """Mark a client as ready after they acknowledge connection"""
        for ws_id, info in self.websocket_registry.items():
            if info["websocket"] == websocket:
                session_code = info["session_code"]
                if (
                    session_code in self.active_connections
                    and ws_id in self.active_connections[session_code]
                ):
                    self.active_connections[session_code][ws_id]["is_ready"] = True
                    logger.info(f"Client {ws_id} marked as ready")
                break

    async def broadcast_player_roster_update(self, session_code: str):
        """Broadcast the authoritative mobile player roster to host clients."""
        mobile_players = self.get_mobile_players(session_code)
        stats = self.get_session_stats(session_code)

        roster_message = {
            "type": "roster_update",
            "data": {
                "session_code": session_code,
                "connected_players": mobile_players,
                "players": mobile_players,
                "total_players": len(mobile_players),
                "connection_stats": stats,
                "server_time_ms": self._utc_now_ms(),
                "timestamp": datetime.now().isoformat(),
            },
        }

        await self.broadcast_to_session(
            session_code,
            roster_message,
            exclude_client_types=["mobile"],
            critical=True,
        )

        logger.debug(
            f"📋 Broadcasted roster update to session {session_code}: {len(mobile_players)} players - {[p['player_name'] for p in mobile_players]}"
        )

    async def wait_for_ready_connections(self, session_code: str, timeout: float = 2.0):
        """Wait for all connections to be ready before proceeding with critical broadcasts"""
        start_time = datetime.now()

        while (datetime.now() - start_time).total_seconds() < timeout:
            connections = self.get_session_connections(session_code)

            # Check if all connections are ready
            all_ready = all(
                conn.get("is_ready", False) or conn.get("client_type") == "web"
                for conn in connections.values()
            )

            if all_ready:
                logger.info(f"All connections ready for session {session_code}")
                return True

            await asyncio.sleep(0.1)

        logger.warning(
            f"Timeout waiting for all connections to be ready in session {session_code}"
        )
        return False

    def _start_heartbeat_checker(self):
        """Start the background task to check for stale connections"""

        async def check_stale_connections():
            while True:
                try:
                    await asyncio.sleep(self.HEARTBEAT_CHECK_INTERVAL_SECONDS)
                    stale_websockets = []
                    total_connections = 0
                    now = datetime.now()

                    for session_code, connections in list(
                        self.active_connections.items()
                    ):
                        total_connections += len(connections)
                        for ws_id, conn_info in list(connections.items()):
                            last_heartbeat = conn_info.get("last_heartbeat")
                            if last_heartbeat:
                                stale_threshold = (
                                    self.MOBILE_HEARTBEAT_STALE_SECONDS
                                    if conn_info.get("client_type") == "mobile"
                                    else self.HEARTBEAT_STALE_SECONDS
                                )
                                seconds_since_heartbeat = (
                                    now - last_heartbeat
                                ).total_seconds()
                                if (
                                    seconds_since_heartbeat
                                    > self.HEARTBEAT_DISCONNECTED_SECONDS
                                ):
                                    conn_info["connection_state"] = "disconnected"
                                elif (
                                    seconds_since_heartbeat
                                    > self.HEARTBEAT_UNSTABLE_SECONDS
                                ):
                                    conn_info["connection_state"] = "unstable"

                                if seconds_since_heartbeat > stale_threshold:
                                    player_name = conn_info.get(
                                        "player_name", "Unknown"
                                    )
                                    client_type = conn_info.get(
                                        "client_type", "unknown"
                                    )
                                    logger.warning(
                                        f"💀 Stale connection detected: {client_type} {player_name} (ws_id: {ws_id}) in session {session_code} - Last heartbeat: {seconds_since_heartbeat:.1f}s ago"
                                    )
                                    stale_websockets.append(conn_info["websocket"])

                    if total_connections > 0:
                        logger.debug(
                            f"💓 Heartbeat check: {total_connections} active connections, {len(stale_websockets)} stale"
                        )

                    # Clean up stale connections
                    for ws in stale_websockets:
                        try:
                            await ws.close(code=1001, reason="Connection timeout")
                        except:
                            pass
                        self.disconnect(ws)

                except Exception as e:
                    logger.error(f"Error in heartbeat checker: {e}")

        # Schedule the task
        try:
            loop = asyncio.get_event_loop()
            self._heartbeat_task = loop.create_task(check_stale_connections())
        except RuntimeError:
            # No event loop running yet - this is fine, will start when app starts
            pass

    def _start_automatic_ping(self):
        """Start background task to send automatic pings to all connections"""

        async def send_periodic_pings():
            while True:
                try:
                    await asyncio.sleep(self.PING_INTERVAL_SECONDS)

                    ping_message = {
                        "type": "ping",
                        "serverTime": int(datetime.utcnow().timestamp() * 1000),
                        "auto": True,  # Mark as automatic server ping
                    }

                    total_sent = 0
                    total_failed = 0

                    for session_code, connections in list(
                        self.active_connections.items()
                    ):
                        for ws_id, conn_info in list(connections.items()):
                            try:
                                websocket = conn_info["websocket"]
                                await websocket.send_text(json.dumps(ping_message))
                                total_sent += 1
                            except Exception as e:
                                total_failed += 1
                                logger.debug(f"Failed to send ping to {ws_id}: {e}")

                    if total_sent > 0:
                        logger.debug(
                            f"📡 Sent automatic ping to {total_sent} connections ({total_failed} failed)"
                        )

                except Exception as e:
                    logger.error(f"Error in automatic ping broadcaster: {e}")

        # Schedule the task
        try:
            loop = asyncio.get_event_loop()
            self._ping_task = loop.create_task(send_periodic_pings())
        except RuntimeError:
            # No event loop running yet - this is fine, will start when app starts
            pass

    def queue_question(self, session_code: str, question_data: Dict[str, Any]) -> None:
        """
        Store a question in the session queue for later retrieval.
        This ensures mobile clients can get questions even if they miss the broadcast.
        """
        if session_code not in self.question_queue:
            self.question_queue[session_code] = {}

        question_id = question_data.get("question_id")
        if question_id:
            self.question_queue[session_code][question_id] = {
                "question_data": question_data,
                "queued_at": datetime.now().isoformat(),
            }
            logger.info(f"📥 Queued question {question_id} for session {session_code}")

    def get_current_question(self, session_code: str) -> Optional[Dict[str, Any]]:
        """
        Get the most recently queued question for a session.
        Returns None if no questions are queued.
        """
        if session_code not in self.question_queue:
            return None

        questions = self.question_queue[session_code]
        if not questions:
            return None

        # Return the most recently added question
        latest_question = max(questions.items(), key=lambda x: x[1]["queued_at"])
        logger.info(
            f"📤 Retrieving queued question {latest_question[0]} for session {session_code}"
        )
        return latest_question[1]["question_data"]

    def clear_question_queue(self, session_code: str) -> None:
        """Clear all queued questions for a session (e.g., when game ends)"""
        if session_code in self.question_queue:
            del self.question_queue[session_code]
            logger.info(f"🗑️ Cleared question queue for session {session_code}")

    def get_buzzer_state(self, session_code: str) -> Dict[str, Any]:
        """Return shared per-session buzzer state."""
        return self.buzzer_states.setdefault(
            session_code,
            {
                "current_buzzer_winner": None,
                "frozen_players": set(),
                "question_active": False,
                "current_question_id": None,
                "attempts": [],
            },
        )

    def start_buzzer_question(self, session_code: str, question_id: Optional[str]):
        """Mark a buzzer question active for all connections in the session."""
        state = self.get_buzzer_state(session_code)
        state.update(
            {
                "current_buzzer_winner": None,
                "frozen_players": set(),
                "question_active": True,
                "current_question_id": question_id,
                "attempts": [],
            }
        )
        logger.info(f"Buzzer question active for session {session_code}: {question_id}")
        return state

    def reset_buzzer_state(self, session_code: str):
        """Reset shared buzzer state for a session."""
        self.buzzer_states[session_code] = {
            "current_buzzer_winner": None,
            "frozen_players": set(),
            "question_active": False,
            "current_question_id": None,
            "attempts": [],
        }
        logger.info(f"Reset buzzer state for session {session_code}")

    def set_session_game_type(self, session_code: str, game_type: str):
        """Store the resolved game type for scheduler and reconnect paths."""
        self.session_game_types[session_code] = game_type
        logger.info(f"Session {session_code} game type set to {game_type}")

    def get_session_game_type(self, session_code: str) -> Optional[str]:
        """Return the resolved game type for a session if known."""
        return self.session_game_types.get(session_code)


# Global connection manager instance
manager = ConnectionManager()
