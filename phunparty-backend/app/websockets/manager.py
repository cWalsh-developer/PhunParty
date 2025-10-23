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

        # Store connection info
        connection_info = {
            "websocket": websocket,
            "client_type": client_type,
            "connected_at": datetime.now().isoformat(),
            "player_id": player_id,
            "player_name": player_name,
            "player_photo": player_photo,
            "player_answered": False,
        }

        self.active_connections[session_code][ws_id] = connection_info
        self.websocket_registry[ws_id] = {
            "session_code": session_code,
            "websocket": websocket,
        }

        logger.info(f"Client connected: {client_type} to session {session_code}")

        # Notify other clients about new connection (if mobile player joining)
        if client_type and player_name:
            await self.broadcast_to_session(
                session_code,
                {
                    "type": "player_joined",
                    "data": {
                        "player_id": player_id,
                        "player_name": player_name,
                        "player_photo": player_photo,
                        "timestamp": datetime.now().isoformat(),
                    },
                },
                exclude_client_types=["mobile"],  # Only notify web clients
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

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """Send message to specific WebSocket"""
        try:
            await websocket.send_text(
                json.dumps({**message, "timestamp": datetime.now().timestamp()})
            )
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")

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
    ):
        """Broadcast message to all clients in a session with filtering options"""
        if session_code not in self.active_connections:
            return

        exclude_websockets = exclude_websockets or []
        message_with_timestamp = {**message, "timestamp": datetime.now().timestamp()}

        disconnected_websockets = []

        for ws_id, connection_info in self.active_connections[session_code].items():
            websocket = connection_info["websocket"]
            client_type = connection_info["client_type"]

            # Skip excluded websockets
            if websocket in exclude_websockets:
                continue

            # Filter by client type if specified
            if only_client_types and client_type not in only_client_types:
                continue

            if exclude_client_types and client_type in exclude_client_types:
                continue

            try:
                await websocket.send_text(json.dumps(message_with_timestamp))
            except WebSocketDisconnect:
                disconnected_websockets.append(websocket)
            except Exception as e:
                logger.error(f"Error broadcasting to websocket: {e}")
                disconnected_websockets.append(websocket)

        # Clean up disconnected websockets
        for ws in disconnected_websockets:
            self.disconnect(ws)

    async def broadcast_to_mobile_players(self, session_code: str, message: dict):
        """Broadcast message only to mobile clients"""
        await self.broadcast_to_session(
            session_code, message, only_client_types=["mobile"]
        )

    async def broadcast_to_web_clients(self, session_code: str, message: dict):
        """Broadcast message only to web clients"""
        await self.broadcast_to_session(
            session_code, message, only_client_types=["web"]
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


# Global connection manager instance
manager = ConnectionManager()
