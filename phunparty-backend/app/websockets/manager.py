"""
WebSocket Connection Manager for PhunParty Game Sessions
Handles real-time communication between web UI and mobile app
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for game sessions"""

    def __init__(self):
        # session_code -> {websocket_id: {websocket, client_type, player_info}}
        self.active_connections: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # websocket_id -> {session_code, websocket}
        self.websocket_registry: Dict[str, Dict[str, Any]] = {}
        # Question queue: session_code -> {question_id: question_data}
        # Stores questions that have been broadcast so mobile clients can retrieve them
        self.question_queue: Dict[str, Dict[str, Any]] = {}
        # Start heartbeat checker and automatic ping broadcaster
        self._heartbeat_task = None
        self._ping_task = None
        self._start_heartbeat_checker()
        self._start_automatic_ping()

    def generate_websocket_id(self, websocket: WebSocket) -> str:
        """Generate unique ID for WebSocket connection"""
        return f"ws_{id(websocket)}_{datetime.now().timestamp()}"

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
            # Don't continue if we can't confirm connection
            return

        # Notify other clients about new connection IMMEDIATELY (if mobile player joining)
        # REMOVED: await asyncio.sleep(0.2) - This delay was causing web UI to miss player joins
        if client_type == "mobile" and player_name:
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

            # CRITICAL: Broadcast player_joined IMMEDIATELY - no delay
            # Web clients should already be listening since they connected first
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

            # CRITICAL: Send roster update to ALL clients (web + mobile)
            # This ensures everyone has the latest player list
            await self.broadcast_player_roster_update(session_code)

            logger.info(
                f"✅ Sent roster_update to all clients in session {session_code}"
            )

            # Add small delay AFTER broadcasting to let the web UI process the updates
            # This ensures the web client receives and processes the messages
            await asyncio.sleep(0.05)  # Minimal delay just to ensure message delivery

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

            # Notify other clients if a mobile player left
            if (
                client_info
                and client_info.get("client_type") == "mobile"
                and client_info.get("player_name")
            ):
                asyncio.create_task(
                    self.broadcast_to_session(
                        session_code,
                        {
                            "type": "player_left",
                            "data": {
                                "player_id": client_info.get("player_id"),
                                "player_name": client_info.get("player_name"),
                                "timestamp": datetime.now().isoformat(),
                            },
                        },
                        exclude_client_types=["mobile"],
                    )
                )

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

    async def broadcast_to_session(
        self,
        session_code: str,
        message: dict,
        exclude_websockets: Optional[List[WebSocket]] = None,
        only_client_types: Optional[List[str]] = None,
        exclude_client_types: Optional[List[str]] = None,
        critical: bool = False,
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
        message_id = f"{message.get('type')}_{datetime.now().timestamp()}"
        message_with_timestamp["message_id"] = message_id

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

        logger.info(
            f"📡 Broadcasting '{message.get('type')}' to session {session_code}{filter_info}"
        )

        for ws_id, connection_info in self.active_connections[session_code].items():
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

        logger.info(
            f"✅ Broadcast complete: {success_count}/{total_targets} clients received '{message.get('type')}' (📱mobile: {mobile_sent}, 💻web: {web_sent})"
        )

        # Clean up disconnected websockets
        for ws in disconnected_websockets:
            self.disconnect(ws)

    async def broadcast_to_mobile_players(self, session_code: str, message: dict):
        """Broadcast message only to mobile clients"""
        session_connections = self.active_connections.get(session_code, {})
        mobile_connections = [
            conn
            for conn in session_connections.values()
            if conn["client_type"] == "mobile"
        ]
        mobile_count = len(mobile_connections)

        logger.info(
            f"📱 Broadcasting to {mobile_count} mobile client(s) in session {session_code}: type={message.get('type')}"
        )

        if mobile_count == 0:
            logger.warning(f"⚠️ NO MOBILE CLIENTS connected to session {session_code}!")
        else:
            # Log details about connected mobile clients
            for conn in mobile_connections:
                logger.info(
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
        logger.info(
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
        mobile_players = []

        for connection_info in connections.values():
            if connection_info["client_type"] == "mobile" and connection_info.get(
                "player_name"
            ):
                mobile_players.append(
                    {
                        "player_id": connection_info.get("player_id"),
                        "player_name": connection_info.get("player_name"),
                        "player_photo": connection_info.get("player_photo"),
                        "connected_at": connection_info.get("connected_at"),
                        "player_answered": connection_info.get("player_answered", None),
                    }
                )

        return mobile_players

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
                "players": 0,
                "hosts": 0,
                "observers": 0,
                "player_breakdown": {},
            }

        connections = self.active_connections[session_code]
        player_breakdown = {}
        hosts = 0
        observers = 0

        for ws_id, conn_info in connections.items():
            client_type = conn_info.get("client_type", "unknown")
            player_id = conn_info.get("player_id")

            if client_type == "host":
                hosts += 1
            elif client_type == "observer":
                observers += 1
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
                logger.info(
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

        logger.info(
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
        """Broadcast current player roster to all clients"""
        mobile_players = self.get_mobile_players(session_code)

        roster_message = {
            "type": "roster_update",
            "data": {
                "players": mobile_players,
                "total_players": len(mobile_players),
                "timestamp": datetime.now().isoformat(),
            },
        }

        await self.broadcast_to_session(
            session_code,
            roster_message,
            critical=True,
        )

        logger.info(
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
                    await asyncio.sleep(20)  # Check every 20 seconds
                    stale_threshold = (
                        45  # Consider stale after 45 seconds of no heartbeat
                    )

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
                                seconds_since_heartbeat = (
                                    now - last_heartbeat
                                ).total_seconds()

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
                    await asyncio.sleep(15)  # Send ping every 15 seconds

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


# Global connection manager instance
manager = ConnectionManager()
