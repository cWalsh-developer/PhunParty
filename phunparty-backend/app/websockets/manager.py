"""
WebSocket Connection Manager for PhunParty Game Sessions
Handles real-time communication between web UI and mobile app
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union

from app.security.loggingUtils import safe_player_ref
from app.security.roster_identity import make_roster_player_id
from app.websockets.redis_bus import websocket_bus
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

BUZZER_CLAIM_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then
    return 0
end

local state = cjson.decode(raw)
if state['accepting_buzzes'] ~= true then
    return 0
end
if state['question_active'] ~= true then
    return 0
end
if state['transitioning'] == true then
    return 0
end
if state['current_question_id'] ~= ARGV[2] then
    return 0
end

local winner = state['current_buzzer_winner']
if winner ~= nil and winner ~= cjson.null and winner ~= '' then
    return 0
end

state['current_buzzer_winner'] = ARGV[1]
state['question_active'] = true
state['transitioning'] = false
state['accepting_buzzes'] = false
state['updated_at'] = ARGV[3]

redis.call('SET', KEYS[1], cjson.encode(state), 'EX', ARGV[4])
return 1
"""


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
    PRESENCE_KEY_TTL_SECONDS = 600
    SHARED_STATE_TTL_SECONDS = 7200
    HEARTBEAT_UNSTABLE_SECONDS = 20
    HEARTBEAT_DISCONNECTED_SECONDS = 60
    PING_INTERVAL_SECONDS = 10
    MOBILE_DISCONNECT_GRACE_SECONDS = 30
    TERMINAL_SESSION_TTL_SECONDS = 900
    ACK_RETRY_DELAY_SECONDS = 1.5
    ACK_MAX_RESENDS = 2
    ROSTER_UPDATE_DEBOUNCE_SECONDS = 0.05
    GENERATION_RENEW_INTERVAL_SECONDS = 60
    ACK_EVENT_TYPES = {
        "game_started",
        "countdown_started",
        "question_started",
        "game_ended",
    }
    WEB_PRIVATE_ID_KEYS = {
        "player_id",
        "player_ids",
        "owner_player_id",
        "sender_player_id",
        "receiver_player_id",
        "recipient_player_id",
        "actor_player_id",
        "current_buzzer_winner",
        "frozen_players",
    }

    def __init__(self):
        # session_code -> {websocket_id: {websocket, client_type, player_info}}
        self.active_connections: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # websocket_id -> {session_code, websocket}
        self.websocket_registry: Dict[str, Dict[str, Any]] = {}
        # id(websocket) -> websocket_id. Keeps hot heartbeat/ACK lookups O(1).
        self.websocket_to_ws_id: Dict[int, str] = {}
        # (session_code, player_id) -> websocket_ids for targeted player sends.
        self.player_connection_index: Dict[tuple[str, str], Set[str]] = {}
        # Question queue: session_code -> {question_id: question_data}
        # Stores questions that have been broadcast so mobile clients can retrieve them
        self.question_queue: Dict[str, Dict[str, Any]] = {}
        # session_code -> authoritative phase/timing snapshot.
        self.session_phase_state: Dict[str, Dict[str, Any]] = {}
        # session_code -> shared buzzer state. Handlers are per-connection, so
        # buzzer state must live at session scope.
        self.buzzer_states: Dict[str, Dict[str, Any]] = {}
        # session_code -> shared Beat the Clock state. Each player has their
        # own current question and score during the same timed round.
        self.beat_clock_states: Dict[str, Dict[str, Any]] = {}
        # session_code -> resolved game mode, shared across scheduler/handlers.
        self.session_game_types: Dict[str, str] = {}
        # session_code -> player_id -> frozen question id for Fair Play violations.
        self.fair_play_frozen_players: Dict[str, Dict[str, str]] = {}
        # session_code -> player_id -> Fair Play UI state included in roster payloads.
        self.fair_play_player_status: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # session_code -> player_id -> pending focus-loss report under grace period.
        self.pending_focus_losses: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # event_id -> delivery/ack state for critical phase messages.
        # session_code -> terminal/final session snapshot kept after game end
        self.terminal_sessions: Dict[str, Dict[str, Any]] = {}
        self.pending_acks: Dict[str, Dict[str, Any]] = {}
        self._missing_connection_warning_at: Dict[str, float] = {}
        # session_code:player_id values for players who explicitly left.
        self.intentional_leaves: Set[str] = set()
        # player leave tasks: "session_code:player_id" -> asyncio.Task
        # Used to avoid flapping presence when mobile networks briefly disconnect.
        self.pending_player_leave_tasks: Dict[str, asyncio.Task] = {}
        self.roster_update_tasks: Dict[str, asyncio.Task] = {}
        # Start heartbeat checker and automatic ping broadcaster
        self._heartbeat_task = None
        self._ping_task = None
        self._start_heartbeat_checker()
        self._start_automatic_ping()

    def _websocket_lookup_key(self, websocket: WebSocket) -> int:
        return id(websocket)

    def _register_connection_indexes(
        self,
        session_code: str,
        ws_id: str,
        connection_info: Dict[str, Any],
    ) -> None:
        websocket = connection_info.get("websocket")
        if websocket:
            self.websocket_to_ws_id[self._websocket_lookup_key(websocket)] = ws_id

        if connection_info.get("client_type") == "mobile" and connection_info.get(
            "player_id"
        ):
            player_key = (session_code, connection_info["player_id"])
            self.player_connection_index.setdefault(player_key, set()).add(ws_id)

    def _remove_connection_indexes(
        self,
        session_code: Optional[str],
        ws_id: Optional[str],
        connection_info: Optional[Dict[str, Any]],
    ) -> None:
        if not ws_id:
            return

        websocket = (connection_info or {}).get("websocket")
        if not websocket and ws_id in self.websocket_registry:
            websocket = self.websocket_registry[ws_id].get("websocket")
        if websocket:
            self.websocket_to_ws_id.pop(self._websocket_lookup_key(websocket), None)

        if (
            session_code
            and connection_info
            and connection_info.get("client_type") == "mobile"
            and connection_info.get("player_id")
        ):
            player_key = (session_code, connection_info["player_id"])
            ws_ids = self.player_connection_index.get(player_key)
            if ws_ids:
                ws_ids.discard(ws_id)
                if not ws_ids:
                    self.player_connection_index.pop(player_key, None)

    def _ws_id_for_websocket(self, websocket: WebSocket) -> Optional[str]:
        ws_id = self.websocket_to_ws_id.get(self._websocket_lookup_key(websocket))
        if ws_id and ws_id in self.websocket_registry:
            return ws_id

        for registry_ws_id, info in self.websocket_registry.items():
            if info.get("websocket") == websocket:
                self.websocket_to_ws_id[self._websocket_lookup_key(websocket)] = (
                    registry_ws_id
                )
                return registry_ws_id

        return None

    def _connection_info_for_websocket(
        self, websocket: WebSocket
    ) -> Optional[Dict[str, Any]]:
        ws_id = self._ws_id_for_websocket(websocket)
        if ws_id:
            registry_info = self.websocket_registry.get(ws_id)
            session_code = (registry_info or {}).get("session_code")
            connection_info = self.active_connections.get(session_code, {}).get(ws_id)
            if connection_info:
                return connection_info

        return None

    def _sanitize_for_web_client(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self._sanitize_for_web_client(item) for item in value]

        if not isinstance(value, dict):
            return value

        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            if key in self.WEB_PRIVATE_ID_KEYS:
                continue

            sanitized[key] = self._sanitize_for_web_client(item)

        return sanitized

    def _outbound_message_for_connection(
        self, message: Dict[str, Any], connection_info: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if connection_info and connection_info.get("client_type") == "web":
            return self._sanitize_for_web_client(message)

        return message

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
                if self.get_player_connections(
                    session_code, player_id
                ) or self.has_shared_player_connections(session_code, player_id):
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
                            "roster_player_id": make_roster_player_id(
                                session_code, player_id
                            ),
                            "player_name": player_name,
                            "timestamp": datetime.now().isoformat(),
                        },
                    },
                    exclude_client_types=["mobile"],
                    critical=True,
                )

                # Keep all clients in sync after confirmed leave.
                await self.schedule_player_roster_update(session_code)

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
        return int(time.time() * 1000)

    def _utc_now(self) -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def _utc_now_iso(self) -> str:
        return self._utc_now().isoformat() + "Z"

    def _presence_keys(self, session_code: str) -> tuple[str, str]:
        return (
            websocket_bus.key("session", session_code, "presence"),
            websocket_bus.key("session", session_code, "presence-meta"),
        )

    def _beat_clock_keys(self, session_code: str) -> tuple[str, str]:
        return (
            self._shared_state_key(session_code, "beat-clock:meta"),
            self._shared_state_key(session_code, "beat-clock:players"),
        )

    def _beat_clock_finish_key(self, session_code: str) -> str:
        return self._shared_state_key(session_code, "beat-clock:finish")

    def _terminal_session_key(self, session_code: str) -> str:
        return websocket_bus.key("terminal", session_code)

    def _fair_play_status_key(self, session_code: str) -> str:
        return self._shared_state_key(session_code, "fair-play-status-by-player")

    def _fair_play_frozen_key(self, session_code: str) -> str:
        return self._shared_state_key(session_code, "fair-play-frozen-by-player")

    def _pending_focus_key(self, session_code: str) -> str:
        return self._shared_state_key(session_code, "pending-focus-by-player")

    def _player_generation_key(self, session_code: str, player_id: str) -> str:
        return websocket_bus.key(
            "session", session_code, "player-generation", player_id
        )

    def _presence_member(self, ws_id: str) -> str:
        return f"{websocket_bus.worker_id}:{ws_id}"

    def _connection_generation(self, ws_id: str) -> str:
        return f"{websocket_bus.worker_id}:{ws_id}"

    def _presence_expiry(self, connection_info: Dict[str, Any]) -> int:
        stale_seconds = (
            self.MOBILE_HEARTBEAT_STALE_SECONDS
            if connection_info.get("client_type") == "mobile"
            else self.HEARTBEAT_STALE_SECONDS
        )
        return int(time.time() + stale_seconds)

    def _presence_metadata(
        self,
        session_code: str,
        ws_id: str,
        connection_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        player_id = connection_info.get("player_id")
        metadata = {
            "member": self._presence_member(ws_id),
            "worker_id": websocket_bus.worker_id,
            "ws_id": ws_id,
            "session_code": session_code,
            "client_type": connection_info.get("client_type"),
            "connected_at": connection_info.get("connected_at"),
            "player_id": player_id,
            "roster_player_id": make_roster_player_id(session_code, player_id),
            "player_name": connection_info.get("player_name")
            or player_id
            or "Unknown player",
            "player_photo": connection_info.get("player_photo"),
            "player_answered": connection_info.get("player_answered", False),
            "connection_generation": connection_info.get("connection_generation"),
            "connection_state": connection_info.get("connection_state", "connected"),
            "is_ready": bool(connection_info.get("is_ready", False)),
            "connection_confirmed": bool(
                connection_info.get("connection_confirmed", False)
            ),
            "updated_at": self._utc_now_iso(),
        }
        if player_id:
            metadata.update(
                self.fair_play_player_status.get(session_code, {}).get(player_id, {})
            )
        return metadata

    def _upsert_presence(
        self,
        session_code: str,
        ws_id: str,
        connection_info: Dict[str, Any],
    ) -> None:
        client = websocket_bus.sync_client
        if not client:
            return

        presence_key, meta_key = self._presence_keys(session_code)
        member = self._presence_member(ws_id)
        metadata = self._presence_metadata(session_code, ws_id, connection_info)
        try:
            pipe = client.pipeline()
            pipe.zadd(presence_key, {member: self._presence_expiry(connection_info)})
            pipe.hset(meta_key, member, json.dumps(metadata, separators=(",", ":")))
            pipe.expire(presence_key, self.PRESENCE_KEY_TTL_SECONDS)
            pipe.expire(meta_key, self.PRESENCE_KEY_TTL_SECONDS)
            pipe.execute()
        except Exception:
            logger.exception("Failed to upsert shared presence for %s", session_code)

    def _remove_presence(self, session_code: str, ws_id: str) -> None:
        client = websocket_bus.sync_client
        if not client:
            return

        presence_key, meta_key = self._presence_keys(session_code)
        member = self._presence_member(ws_id)
        try:
            pipe = client.pipeline()
            pipe.zrem(presence_key, member)
            pipe.hdel(meta_key, member)
            pipe.execute()
        except Exception:
            logger.exception("Failed to remove shared presence for %s", session_code)

    def _cleanup_expired_shared_presence(
        self, presence_key: str, meta_key: str, now: int
    ) -> int:
        client = websocket_bus.sync_client
        if not client:
            return 0

        script = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, member in ipairs(expired) do
    redis.call('ZREM', KEYS[1], member)
    redis.call('HDEL', KEYS[2], member)
end
return #expired
"""
        try:
            return int(client.eval(script, 2, presence_key, meta_key, str(now)) or 0)
        except Exception:
            logger.exception("Failed to cleanup expired shared presence metadata")
            return 0

    def _shared_presence_metadata(self, session_code: str) -> List[Dict[str, Any]]:
        client = websocket_bus.sync_client
        if not client:
            return []

        presence_key, meta_key = self._presence_keys(session_code)
        now = int(time.time())
        try:
            self._cleanup_expired_shared_presence(presence_key, meta_key, now)
            members = client.zrangebyscore(presence_key, now + 1, "+inf")
        except Exception:
            logger.exception("Failed to read shared presence for %s", session_code)
            return []
        if not members:
            return []

        try:
            raw_values = client.hmget(meta_key, members)
        except Exception:
            logger.exception(
                "Failed to read shared presence metadata for %s", session_code
            )
            return []
        metadata: List[Dict[str, Any]] = []
        stale_members = []
        for member, raw_value in zip(members, raw_values):
            if not raw_value:
                stale_members.append(member)
                continue
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                stale_members.append(member)
                continue
            metadata.append(parsed)

        if stale_members:
            try:
                pipe = client.pipeline()
                pipe.zrem(presence_key, *stale_members)
                pipe.hdel(meta_key, *stale_members)
                pipe.execute()
            except Exception:
                logger.exception(
                    "Failed to cleanup stale shared presence for %s", session_code
                )

        return metadata

    def _claim_player_connection_generation(
        self, session_code: str, player_id: str, generation: str
    ) -> Optional[str]:
        client = websocket_bus.sync_client
        if not client:
            return None

        key = self._player_generation_key(session_code, player_id)
        script = """
local old = redis.call('GET', KEYS[1])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return old
"""
        try:
            old_generation = client.eval(
                script,
                1,
                key,
                generation,
                str(self.PRESENCE_KEY_TTL_SECONDS),
            )
            return str(old_generation) if old_generation else None
        except Exception:
            logger.exception(
                "Failed to claim player connection generation for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )
            if websocket_bus.connected:
                raise
            return None

    async def _claim_player_connection_generation_async(
        self, session_code: str, player_id: str, generation: str
    ) -> Optional[str]:
        client = websocket_bus.async_client
        if not client:
            return self._claim_player_connection_generation(
                session_code,
                player_id,
                generation,
            )

        key = self._player_generation_key(session_code, player_id)
        script = """
local old = redis.call('GET', KEYS[1])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return old
"""
        try:
            old_generation = await client.eval(
                script,
                1,
                key,
                generation,
                str(self.PRESENCE_KEY_TTL_SECONDS),
            )
            return str(old_generation) if old_generation else None
        except Exception:
            logger.exception(
                "Failed to claim player connection generation for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )
            if websocket_bus.connected:
                raise
            return None

    def _get_player_connection_generation(
        self, session_code: str, player_id: str
    ) -> Optional[str]:
        client = websocket_bus.sync_client
        if not client:
            return None
        try:
            value = client.get(self._player_generation_key(session_code, player_id))
            return str(value) if value else None
        except Exception:
            logger.exception(
                "Failed to read player connection generation for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )
            return None

    async def _get_player_connection_generation_async(
        self, session_code: str, player_id: str
    ) -> Optional[str]:
        client = websocket_bus.async_client
        if not client:
            return self._get_player_connection_generation(session_code, player_id)
        try:
            value = await client.get(
                self._player_generation_key(session_code, player_id)
            )
            return str(value) if value else None
        except Exception:
            logger.exception(
                "Failed to read player connection generation for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )
            return None

    def _clear_player_connection_generation(
        self, session_code: str, player_id: str, generation: Optional[str]
    ) -> None:
        client = websocket_bus.sync_client
        if not client or not generation:
            return

        key = self._player_generation_key(session_code, player_id)
        script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
        try:
            client.eval(script, 1, key, generation)
        except Exception:
            logger.exception(
                "Failed to clear player connection generation for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )

    async def _clear_player_connection_generation_async(
        self, session_code: str, player_id: str, generation: Optional[str]
    ) -> None:
        client = websocket_bus.async_client
        if not client:
            self._clear_player_connection_generation(
                session_code,
                player_id,
                generation,
            )
            return
        if not generation:
            return

        key = self._player_generation_key(session_code, player_id)
        script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
        try:
            await client.eval(script, 1, key, generation)
        except Exception:
            logger.exception(
                "Failed to clear player connection generation for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )

    async def _renew_player_connection_generation_async(
        self, session_code: str, player_id: str, generation: Optional[str]
    ) -> bool:
        client = websocket_bus.async_client
        if not client:
            return True
        if not generation:
            return False

        key = self._player_generation_key(session_code, player_id)
        script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
        try:
            renewed = await client.eval(
                script,
                1,
                key,
                generation,
                str(self.PRESENCE_KEY_TTL_SECONDS),
            )
            return bool(renewed)
        except Exception:
            logger.exception(
                "Failed to renew player connection generation for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )
            return False

    async def _renew_or_disconnect_generation(
        self,
        session_code: str,
        player_id: str,
        generation: Optional[str],
    ) -> None:
        renewed = await self._renew_player_connection_generation_async(
            session_code,
            player_id,
            generation,
        )
        if renewed:
            return

        await self._disconnect_local_generation(
            session_code,
            player_id,
            generation or "",
            close_code=4000,
            reason="Connection lease expired",
        )

    def _update_shared_presence_metadata(
        self,
        session_code: str,
        predicate,
        updater,
    ) -> int:
        client = websocket_bus.sync_client
        if not client:
            return 0

        presence_key, meta_key = self._presence_keys(session_code)
        now = int(time.time())
        try:
            self._cleanup_expired_shared_presence(presence_key, meta_key, now)
            members = client.zrangebyscore(presence_key, now + 1, "+inf")
            if not members:
                return 0

            raw_values = client.hmget(meta_key, members)
            pipe = client.pipeline()
            updated = 0
            for member, raw_value in zip(members, raw_values):
                if not raw_value:
                    continue
                try:
                    metadata = json.loads(raw_value)
                except json.JSONDecodeError:
                    continue
                if not predicate(metadata):
                    continue

                updater(metadata)
                metadata["updated_at"] = self._utc_now_iso()
                pipe.hset(meta_key, member, json.dumps(metadata, separators=(",", ":")))
                updated += 1

            if updated:
                pipe.expire(meta_key, self.PRESENCE_KEY_TTL_SECONDS)
                pipe.execute()
            return updated
        except Exception:
            logger.exception("Failed to update shared presence for %s", session_code)
            return 0

    def has_shared_player_connections(self, session_code: str, player_id: str) -> bool:
        return any(
            metadata.get("client_type") == "mobile"
            and metadata.get("player_id") == player_id
            and metadata.get("connection_state")
            not in {"fair_play_focus_lost", "disconnected"}
            for metadata in self._shared_presence_metadata(session_code)
        )

    def _shared_state_key(self, session_code: str, name: str) -> str:
        return websocket_bus.key("session", session_code, name)

    def _redis_json_get(self, key: str) -> Optional[Dict[str, Any]]:
        client = websocket_bus.sync_client
        if not client:
            return None
        try:
            raw_value = client.get(key)
            return json.loads(raw_value) if raw_value else None
        except Exception:
            logger.exception("Failed to read shared state key %s", key)
            return None

    def _redis_json_set(self, key: str, value: Dict[str, Any]) -> None:
        client = websocket_bus.sync_client
        if not client:
            return
        try:
            client.set(
                key,
                json.dumps(value, separators=(",", ":")),
                ex=self.SHARED_STATE_TTL_SECONDS,
            )
        except Exception:
            logger.exception("Failed to write shared state key %s", key)

    def _redis_delete(self, *keys: str) -> None:
        client = websocket_bus.sync_client
        if not client or not keys:
            return
        try:
            client.delete(*keys)
        except Exception:
            logger.exception("Failed to delete shared state keys %s", keys)

    def _redis_expire(self, *keys: str, ttl_seconds: int) -> None:
        client = websocket_bus.sync_client
        if not client or not keys:
            return
        try:
            pipe = client.pipeline()
            for key in keys:
                pipe.expire(key, ttl_seconds)
            pipe.execute()
        except Exception:
            logger.exception("Failed to expire shared state keys %s", keys)

    def _redis_hash_json_get(self, key: str, field: str) -> Optional[Dict[str, Any]]:
        client = websocket_bus.sync_client
        if not client:
            return None
        try:
            raw_value = client.hget(key, field)
            return json.loads(raw_value) if raw_value else None
        except Exception:
            logger.exception("Failed to read shared hash field %s/%s", key, field)
            return None

    def _redis_hash_json_set(self, key: str, field: str, value: Dict[str, Any]) -> None:
        client = websocket_bus.sync_client
        if not client:
            return
        try:
            pipe = client.pipeline()
            pipe.hset(key, field, json.dumps(value, separators=(",", ":")))
            pipe.expire(key, self.SHARED_STATE_TTL_SECONDS)
            pipe.execute()
        except Exception:
            logger.exception("Failed to write shared hash field %s/%s", key, field)

    def _redis_hash_get(self, key: str, field: str) -> Optional[str]:
        client = websocket_bus.sync_client
        if not client:
            return None
        try:
            return client.hget(key, field)
        except Exception:
            logger.exception("Failed to read shared hash field %s/%s", key, field)
            return None

    def _redis_hash_all(self, key: str) -> Dict[str, str]:
        client = websocket_bus.sync_client
        if not client:
            return {}
        try:
            return dict(client.hgetall(key))
        except Exception:
            logger.exception("Failed to read shared hash %s", key)
            return {}

    def _redis_hash_set(self, key: str, field: str, value: str) -> None:
        client = websocket_bus.sync_client
        if not client:
            return
        try:
            pipe = client.pipeline()
            pipe.hset(key, field, value)
            pipe.expire(key, self.SHARED_STATE_TTL_SECONDS)
            pipe.execute()
        except Exception:
            logger.exception("Failed to write shared hash field %s/%s", key, field)

    def _redis_hash_delete(self, key: str, field: str) -> None:
        client = websocket_bus.sync_client
        if not client:
            return
        try:
            client.hdel(key, field)
        except Exception:
            logger.exception("Failed to delete shared hash field %s/%s", key, field)

    def _serialize_buzzer_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        serialized = dict(state)
        frozen_players = serialized.get("frozen_players", set())
        if isinstance(frozen_players, set):
            serialized["frozen_players"] = sorted(frozen_players)
        serialized["updated_at"] = self._utc_now_iso()
        return serialized

    def _deserialize_buzzer_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        deserialized = dict(state)
        frozen_players = deserialized.get("frozen_players", [])
        if not isinstance(frozen_players, set):
            deserialized["frozen_players"] = set(frozen_players or [])
        return deserialized

    def _json_safe_state(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._json_safe_state(item)
                for key, item in value.items()
                if not str(key).endswith("_dt")
            }
        if isinstance(value, list):
            return [self._json_safe_state(item) for item in value]
        if isinstance(value, set):
            return sorted(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def save_buzzer_state(self, session_code: str, state: Dict[str, Any]) -> None:
        self.buzzer_states[session_code] = state
        self._redis_json_set(
            self._shared_state_key(session_code, "buzzer"),
            self._serialize_buzzer_state(state),
        )

    def claim_buzzer_winner(
        self,
        session_code: str,
        player_id: str,
        question_id: str,
    ) -> bool:
        client = websocket_bus.sync_client
        state = self.get_buzzer_state(session_code)

        if not client:
            if (
                not state.get("accepting_buzzes")
                or not state.get("question_active")
                or state.get("transitioning")
                or state.get("current_question_id") != question_id
                or state.get("current_buzzer_winner")
            ):
                return False
            state["current_buzzer_winner"] = player_id
            state["question_active"] = True
            state["transitioning"] = False
            state["accepting_buzzes"] = False
            self.save_buzzer_state(session_code, state)
            return True

        try:
            won = client.eval(
                BUZZER_CLAIM_SCRIPT,
                1,
                self._shared_state_key(session_code, "buzzer"),
                player_id,
                question_id,
                self._utc_now_iso(),
                self.SHARED_STATE_TTL_SECONDS,
            )
        except Exception:
            logger.exception("Failed to claim buzzer winner in Redis")
            return False

        if int(won or 0) != 1:
            return False

        shared_state = self._redis_json_get(
            self._shared_state_key(session_code, "buzzer")
        )
        if shared_state:
            self.buzzer_states[session_code] = self._deserialize_buzzer_state(
                shared_state
            )
        return True

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
        state.update(
            {key: value for key, value in updates.items() if value is not None}
        )
        self._redis_json_set(
            self._shared_state_key(session_code, "phase"),
            state,
        )
        logger.info(f"Session {session_code} phase set to {phase_value}")
        return dict(state)

    def get_session_phase_state(self, session_code: str) -> Dict[str, Any]:
        """Return the authoritative phase snapshot, defaulting to lobby."""
        shared_state = self._redis_json_get(
            self._shared_state_key(session_code, "phase")
        )
        if shared_state:
            self.session_phase_state[session_code] = shared_state
            return {**shared_state, "server_time_ms": self._utc_now_ms()}

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
        ws_id = self._ws_id_for_websocket(websocket)

        if not ws_id:
            logger.warning(f"ACK received for {event_id} from unknown websocket")
            return False

        event_state = self.pending_acks.get(event_id)
        if not event_state:
            logger.debug(f"ACK received for unknown or completed event {event_id}")
            return False

        target_state = event_state["targets"].get(ws_id)
        if not target_state:
            logger.debug(
                f"ACK received for {event_id} from non-target websocket {ws_id}"
            )
            return False

        target_state["acked"] = True
        target_state["acked_at"] = self._utc_now_iso()
        logger.debug(f"ACK received for {event_id} from {ws_id}")

        if all(target.get("acked") for target in event_state["targets"].values()):
            logger.debug(f"All targets acknowledged {event_id}")
            self.pending_acks.pop(event_id, None)

        return True

    def get_pending_ack_summary(
        self, session_code: Optional[str] = None
    ) -> Dict[str, Any]:
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

                connection_info = self.active_connections.get(session_code, {}).get(
                    ws_id
                )
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
    ) -> bool:
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
        connection_generation = self._connection_generation(ws_id)

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
            "connection_generation": connection_generation,
            "is_ready": False,  # Track if client acknowledged connection
            "connection_confirmed": False,
        }

        self.active_connections[session_code][ws_id] = connection_info
        self.websocket_registry[ws_id] = {
            "session_code": session_code,
            "websocket": websocket,
        }
        self._register_connection_indexes(session_code, ws_id, connection_info)
        self._upsert_presence(session_code, ws_id, connection_info)

        logger.info(
            f"Client connected: {client_type} to session {session_code} (ws_id: {ws_id}, player: {player_name or 'N/A'})"
        )
        logger.info(
            "CONNECT DEBUG session=%s client_type=%s player_ref=%s player_name=%s player_photo=%s",
            session_code,
            client_type,
            safe_player_ref(player_id),
            player_name,
            player_photo,
        )

        # Send connection confirmation to the connecting client and wait for ack
        try:
            connection_established_message = {
                "type": "connection_established",
                "data": {
                    "ws_id": ws_id,
                    "session_code": session_code,
                    "client_type": client_type,
                    "player_id": player_id,
                    "roster_player_id": make_roster_player_id(session_code, player_id),
                    "player_name": player_name,
                    "timestamp": datetime.now().isoformat(),
                    "requires_ack": True,
                },
                "timestamp": datetime.now().timestamp(),
            }
            await websocket.send_text(
                json.dumps(
                    self._outbound_message_for_connection(
                        connection_established_message,
                        connection_info,
                    )
                )
            )

            # Mark connection as confirmed after successful send
            connection_info["connection_confirmed"] = True
            old_generation = None
            if client_type == "mobile" and player_id:
                old_generation = await self._claim_player_connection_generation_async(
                    session_code,
                    player_id,
                    connection_generation,
                )
            self._upsert_presence(session_code, ws_id, connection_info)
            logger.info(
                f"Connection confirmation sent to {client_type} client (ws_id: {ws_id})"
            )
            if (
                client_type == "mobile"
                and player_id
                and old_generation
                and old_generation != connection_generation
            ):
                await websocket_bus.publish(
                    {
                        "kind": "revoke_connection_generation",
                        "session_code": session_code,
                        "player_id": player_id,
                        "generation": old_generation,
                        "close_code": 4000,
                        "reason": "New connection established",
                    }
                )

        except Exception as e:
            logger.error(f"Failed to send connection confirmation: {e}")
            # Clean up partially registered connection to avoid ghost players.
            self.disconnect(websocket)
            return False

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
                            "roster_player_id": make_roster_player_id(
                                session_code, player_id
                            ),
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

            # Coalesce roster rebuilds during join/reconnect bursts.
            await self.schedule_player_roster_update(session_code)

            logger.info(
                f"✅ Sent roster_update to all clients in session {session_code}"
            )

            logger.info(
                f"✅ Mobile join flow completed for {player_name} in session {session_code}"
            )

        return True

    def disconnect(self, websocket: WebSocket):
        """Disconnect a client"""
        ws_id = None
        session_code = None
        client_info = None

        ws_id = self._ws_id_for_websocket(websocket)
        if ws_id:
            registry_info = self.websocket_registry.get(ws_id, {})
            session_code = registry_info.get("session_code")
            client_info = self.active_connections.get(session_code, {}).get(ws_id)

        if ws_id and session_code:
            if (
                client_info
                and client_info.get("client_type") == "mobile"
                and client_info.get("player_id")
            ):
                player_id = client_info.get("player_id")
                if self.should_suppress_leave_for_fair_play(session_code, player_id):
                    logger.info(
                        "Suppressing mobile disconnect removal for Fair Play focus loss: session=%s player=%s",
                        session_code,
                        player_id,
                    )
                    client_info["connection_state"] = "fair_play_focus_lost"
                    client_info["last_heartbeat"] = datetime.now()
                    self._upsert_presence(session_code, ws_id, client_info)
                    self.update_fair_play_status(
                        session_code,
                        player_id,
                        connection_state="fair_play_focus_lost",
                        answer_status="pending_fair_play_grace",
                    )
                    return

            # Remove from connections
            if session_code in self.active_connections:
                if ws_id in self.active_connections[session_code]:
                    del self.active_connections[session_code][ws_id]

                # Clean up empty session
                if not self.active_connections[session_code]:
                    del self.active_connections[session_code]

            # Remove from registry
            self._remove_connection_indexes(session_code, ws_id, client_info)
            self.websocket_registry.pop(ws_id, None)
            self._remove_presence(session_code, ws_id)
            if (
                client_info
                and client_info.get("client_type") == "mobile"
                and client_info.get("player_id")
            ):
                self._clear_player_connection_generation(
                    session_code,
                    client_info.get("player_id"),
                    client_info.get("connection_generation"),
                )

            logger.info(f"Client disconnected from session {session_code}")

            # For mobile clients, delay leave notification to tolerate brief reconnect gaps.
            if (
                client_info
                and client_info.get("client_type") == "mobile"
                and client_info.get("player_id")
                and self._player_task_key(session_code, client_info.get("player_id"))
                not in self.intentional_leaves
            ):
                player_id = client_info.get("player_id")
                if self.should_suppress_leave_for_fair_play(session_code, player_id):
                    logger.info(
                        "Suppressing player_left for Fair Play focus loss: session=%s player=%s",
                        session_code,
                        player_id,
                    )
                    return

                self._schedule_mobile_leave(session_code, client_info)

    def should_suppress_leave_for_fair_play(
        self, session_code: str, player_id: Optional[str]
    ) -> bool:
        """Keep roster membership stable during a Fair Play focus-loss grace window."""
        if not player_id:
            return False

        phase_state = self.get_session_phase_state(session_code)
        if phase_state.get("phase") != SessionPhase.QUESTION.value:
            return False

        return bool(self.get_pending_focus_loss(session_code, player_id))

    def remember_terminal_session(
        self,
        session_code: str,
        snapshot: Dict[str, Any],
        ttl_seconds: int = TERMINAL_SESSION_TTL_SECONDS,
    ) -> Dict[str, Any]:
        """Keep final session/Fair Play state briefly after the live session ends."""
        expires_at = self._utc_now() + timedelta(seconds=ttl_seconds)

        terminal_snapshot = {
            **snapshot,
            "session_code": session_code,
            "terminal": True,
            "cached_at": self._utc_now_iso(),
            "expires_at": expires_at.isoformat() + "Z",
        }

        self.terminal_sessions[session_code] = terminal_snapshot
        client = websocket_bus.sync_client
        if client:
            try:
                client.set(
                    self._terminal_session_key(session_code),
                    json.dumps(terminal_snapshot, separators=(",", ":")),
                    ex=ttl_seconds,
                )
            except Exception:
                logger.exception(
                    "Failed to cache terminal session snapshot in Redis for %s",
                    session_code,
                )

        logger.info(
            "Cached terminal session snapshot for %s until %s",
            session_code,
            terminal_snapshot["expires_at"],
        )

        return terminal_snapshot

    def get_terminal_session(self, session_code: str) -> Optional[Dict[str, Any]]:
        """Return a terminal session snapshot if it has not expired."""
        client = websocket_bus.sync_client
        if client:
            try:
                raw_snapshot = client.get(self._terminal_session_key(session_code))
                if raw_snapshot:
                    snapshot = json.loads(raw_snapshot)
                    self.terminal_sessions[session_code] = snapshot
                    return dict(snapshot)
            except Exception:
                logger.exception(
                    "Failed to read terminal session snapshot from Redis for %s",
                    session_code,
                )

        snapshot = self.terminal_sessions.get(session_code)

        if not snapshot:
            return None

        expires_at_raw = snapshot.get("expires_at")

        try:
            expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", ""))
        except Exception:
            self.terminal_sessions.pop(session_code, None)
            return None

        if self._utc_now() > expires_at:
            self.terminal_sessions.pop(session_code, None)
            return None

        return dict(snapshot)

    async def cleanup_terminal_session_later(
        self,
        session_code: str,
        delay_seconds: int = 900,
    ) -> None:
        await asyncio.sleep(delay_seconds)
        self.terminal_sessions.pop(session_code, None)
        self._redis_delete(self._terminal_session_key(session_code))
        logger.info("Cleaned terminal session snapshot for %s", session_code)

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
        self.beat_clock_states.pop(session_code, None)
        self.session_game_types.pop(session_code, None)
        self.fair_play_frozen_players.pop(session_code, None)
        self.fair_play_player_status.pop(session_code, None)
        self.pending_focus_losses.pop(session_code, None)
        for player_key in list(self.player_connection_index):
            if player_key[0] == session_code:
                self.player_connection_index.pop(player_key, None)
        roster_task = self.roster_update_tasks.pop(session_code, None)
        if roster_task and not roster_task.done():
            roster_task.cancel()
        self._redis_expire(
            self._shared_state_key(session_code, "phase"),
            self._shared_state_key(session_code, "current-question"),
            self._shared_state_key(session_code, "game-type"),
            self._shared_state_key(session_code, "buzzer"),
            self._shared_state_key(session_code, "beat-clock"),
            self._shared_state_key(session_code, "fair-play-status"),
            self._shared_state_key(session_code, "fair-play-frozen"),
            self._shared_state_key(session_code, "pending-focus"),
            self._fair_play_status_key(session_code),
            self._fair_play_frozen_key(session_code),
            self._pending_focus_key(session_code),
            ttl_seconds=self.TERMINAL_SESSION_TTL_SECONDS,
        )

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
                connection_info = self._connection_info_for_websocket(websocket)
                outbound_message = self._outbound_message_for_connection(
                    {**message, "timestamp": datetime.now().timestamp()},
                    connection_info,
                )
                await websocket.send_text(json.dumps(outbound_message))
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

        ws_id = self._ws_id_for_websocket(websocket)
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

        return sent

    async def _send_local_message_to_player(
        self,
        session_code: str,
        player_id: str,
        message: dict,
        critical: bool = False,
    ) -> None:
        for connection_info in self.get_player_connections(
            session_code,
            player_id,
        ).values():
            websocket = connection_info.get("websocket")
            if not websocket:
                continue

            if critical:
                await self.send_personal_critical_message(
                    session_code,
                    message,
                    websocket,
                )
            else:
                await self.send_personal_message(message, websocket)

    async def send_message_to_player(
        self,
        session_code: str,
        player_id: str,
        message: dict,
        critical: bool = False,
    ) -> None:
        await asyncio.gather(
            self._send_local_message_to_player(
                session_code=session_code,
                player_id=player_id,
                message=message,
                critical=critical,
            ),
            websocket_bus.publish(
                {
                    "kind": "player_message",
                    "session_code": session_code,
                    "player_id": player_id,
                    "message": message,
                    "critical": critical,
                }
            ),
        )

    async def _disconnect_local_player(
        self,
        session_code: str,
        player_id: str,
        *,
        messages: Optional[List[Dict[str, Any]]] = None,
        close_code: int = 4000,
        reason: str = "Disconnected",
    ) -> int:
        disconnected_count = 0
        connections = list(self.get_player_connections(session_code, player_id).items())
        for ws_id, connection_info in connections:
            websocket = connection_info.get("websocket")
            if not websocket:
                continue

            for message in messages or []:
                await self.send_personal_message(message, websocket)

            try:
                await websocket.close(code=close_code, reason=reason)
            except Exception as exc:
                logger.debug("Error closing player websocket %s: %s", ws_id, exc)

            if ws_id in self.active_connections.get(session_code, {}):
                del self.active_connections[session_code][ws_id]
            self._remove_connection_indexes(session_code, ws_id, connection_info)
            self.websocket_registry.pop(ws_id, None)
            self._remove_presence(session_code, ws_id)
            await self._clear_player_connection_generation_async(
                session_code,
                player_id,
                connection_info.get("connection_generation"),
            )
            disconnected_count += 1

        if (
            session_code in self.active_connections
            and not self.active_connections[session_code]
        ):
            self.active_connections.pop(session_code, None)

        return disconnected_count

    async def _disconnect_local_generation(
        self,
        session_code: str,
        player_id: str,
        generation: str,
        *,
        close_code: int = 4000,
        reason: str = "Connection replaced",
    ) -> int:
        disconnected_count = 0
        for ws_id, connection_info in list(
            self.get_player_connections(session_code, player_id).items()
        ):
            if connection_info.get("connection_generation") != generation:
                continue

            websocket = connection_info.get("websocket")
            if websocket:
                try:
                    await websocket.close(code=close_code, reason=reason)
                except Exception as exc:
                    logger.debug("Error closing stale websocket %s: %s", ws_id, exc)

            if ws_id in self.active_connections.get(session_code, {}):
                del self.active_connections[session_code][ws_id]
            self._remove_connection_indexes(session_code, ws_id, connection_info)
            self.websocket_registry.pop(ws_id, None)
            self._remove_presence(session_code, ws_id)
            disconnected_count += 1

        if (
            session_code in self.active_connections
            and not self.active_connections[session_code]
        ):
            self.active_connections.pop(session_code, None)
        return disconnected_count

    def connection_is_current(
        self, websocket: WebSocket, session_code: str, player_id: Optional[str]
    ) -> bool:
        if not player_id:
            return True

        connection_info = self._connection_info_for_websocket(websocket)
        if not connection_info:
            return False

        generation = connection_info.get("connection_generation")
        if not generation:
            return True

        current_generation = self._get_player_connection_generation(
            session_code,
            player_id,
        )
        if current_generation is None and websocket_bus.sync_client:
            return False
        return current_generation is None or current_generation == generation

    async def connection_is_current_async(
        self, websocket: WebSocket, session_code: str, player_id: Optional[str]
    ) -> bool:
        if not player_id:
            return True

        connection_info = self._connection_info_for_websocket(websocket)
        if not connection_info:
            return False

        generation = connection_info.get("connection_generation")
        if not generation:
            return True

        current_generation = await self._get_player_connection_generation_async(
            session_code,
            player_id,
        )
        if current_generation is None and (
            websocket_bus.async_client or websocket_bus.sync_client
        ):
            return False
        return current_generation is None or current_generation == generation

    async def disconnect_player_everywhere(
        self,
        session_code: str,
        player_id: str,
        *,
        messages: Optional[List[Dict[str, Any]]] = None,
        close_code: int = 4000,
        reason: str = "Disconnected",
    ) -> None:
        await asyncio.gather(
            self._disconnect_local_player(
                session_code=session_code,
                player_id=player_id,
                messages=messages,
                close_code=close_code,
                reason=reason,
            ),
            websocket_bus.publish(
                {
                    "kind": "disconnect_player",
                    "session_code": session_code,
                    "player_id": player_id,
                    "messages": messages or [],
                    "close_code": close_code,
                    "reason": reason,
                }
            ),
        )

    async def _broadcast_local_to_session(
        self,
        session_code: str,
        message: dict,
        exclude_websockets: Optional[List[WebSocket]] = None,
        only_client_types: Optional[List[str]] = None,
        exclude_client_types: Optional[List[str]] = None,
        critical: bool = False,
        require_ack: bool = False,
    ):
        """Broadcast message to local clients in a session."""
        if session_code not in self.active_connections:
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
                    outbound_message = self._outbound_message_for_connection(
                        message_with_timestamp,
                        connection_info,
                    )
                    await websocket.send_text(json.dumps(outbound_message))
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

        logger.info(
            "Broadcast complete: %s/%s clients received %s (mobile=%s, web=%s)",
            success_count,
            total_targets,
            message.get("type"),
            mobile_sent,
            web_sent,
        )

        # Clean up disconnected websockets
        for ws in disconnected_websockets:
            self.disconnect(ws)

        if should_require_ack and success_count > 0:
            self._schedule_ack_retry(message_with_timestamp["event_id"])

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
        """Broadcast to local sockets and publish for other workers."""
        await asyncio.gather(
            self._broadcast_local_to_session(
                session_code=session_code,
                message=message,
                exclude_websockets=exclude_websockets,
                only_client_types=only_client_types,
                exclude_client_types=exclude_client_types,
                critical=critical,
                require_ack=require_ack,
            ),
            websocket_bus.publish(
                {
                    "kind": "session_broadcast",
                    "session_code": session_code,
                    "message": message,
                    "only_client_types": only_client_types,
                    "exclude_client_types": exclude_client_types,
                    "critical": critical,
                    "require_ack": require_ack,
                }
            ),
        )

    async def dispatch_bus_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")

        if kind == "session_broadcast":
            await self._broadcast_local_to_session(
                session_code=event["session_code"],
                message=event["message"],
                only_client_types=event.get("only_client_types"),
                exclude_client_types=event.get("exclude_client_types"),
                critical=bool(event.get("critical")),
                require_ack=bool(event.get("require_ack")),
            )
            return

        if kind == "player_message":
            await self._send_local_message_to_player(
                session_code=event["session_code"],
                player_id=event["player_id"],
                message=event["message"],
                critical=bool(event.get("critical")),
            )
            return

        if kind == "disconnect_player":
            await self._disconnect_local_player(
                session_code=event["session_code"],
                player_id=event["player_id"],
                messages=event.get("messages") or [],
                close_code=int(event.get("close_code") or 4000),
                reason=event.get("reason") or "Disconnected",
            )
            return

        if kind == "revoke_connection_generation":
            await self._disconnect_local_generation(
                session_code=event["session_code"],
                player_id=event["player_id"],
                generation=event["generation"],
                close_code=int(event.get("close_code") or 4000),
                reason=event.get("reason") or "Connection replaced",
            )
            return

        logger.warning("Unknown Redis WebSocket event kind: %s", kind)

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
                    "Mobile client: player_ref=%s, ws_id=%s",
                    safe_player_ref(conn.get("player_id")),
                    conn.get("ws_id"),
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

    def _mobile_players_from_shared_presence(
        self, session_code: str, shared_presence: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        latest_by_player: Dict[str, Dict[str, Any]] = {}
        unnamed_mobile_players: List[Dict[str, Any]] = []

        for metadata in shared_presence:
            if metadata.get("client_type") != "mobile":
                continue
            if not metadata.get("connection_confirmed"):
                continue

            player_id = metadata.get("player_id")
            player_data = {
                "player_id": player_id,
                "roster_player_id": metadata.get("roster_player_id")
                or make_roster_player_id(session_code, player_id),
                "player_name": metadata.get("player_name")
                or player_id
                or "Unknown player",
                "player_photo": metadata.get("player_photo"),
                "connected_at": metadata.get("connected_at"),
                "player_answered": metadata.get("player_answered", None),
                "connection_state": metadata.get("connection_state", "connected"),
                "is_ready": metadata.get("is_ready", False),
            }
            for key in (
                "strike_count",
                "max_strikes",
                "is_frozen",
                "frozen_question_id",
                "is_kicked",
                "answer_status",
                "fair_play_reason",
            ):
                if key in metadata:
                    player_data[key] = metadata[key]

            if player_id:
                existing = latest_by_player.get(player_id)
                existing_connected_at = existing.get("connected_at") if existing else ""
                candidate_connected_at = player_data.get("connected_at") or ""
                if not existing or candidate_connected_at >= existing_connected_at:
                    latest_by_player[player_id] = player_data
            else:
                unnamed_mobile_players.append(player_data)

        deduped_players = list(latest_by_player.values()) + unnamed_mobile_players
        deduped_players.sort(
            key=lambda p: (p.get("player_name") or "", p.get("connected_at") or "")
        )
        return deduped_players

    def _mobile_players_from_connections(
        self, session_code: str, connections: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
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
                "roster_player_id": make_roster_player_id(session_code, player_id),
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

        deduped_players = list(latest_by_player.values()) + unnamed_mobile_players
        deduped_players.sort(
            key=lambda p: (p.get("player_name") or "", p.get("connected_at") or "")
        )
        return deduped_players

    def build_roster_snapshot(
        self, session_code: str
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Build roster players and stats from one shared/local presence snapshot."""
        shared_presence = self._shared_presence_metadata(session_code)
        if shared_presence:
            mobile_players = self._mobile_players_from_shared_presence(
                session_code,
                shared_presence,
            )
            web_clients = sum(
                1
                for metadata in shared_presence
                if metadata.get("client_type") == "web"
                and metadata.get("connection_confirmed")
            )
            mobile_clients = sum(
                1
                for metadata in shared_presence
                if metadata.get("client_type") == "mobile"
                and metadata.get("connection_confirmed")
            )
            player_breakdown: Dict[str, Dict[str, Any]] = {}
            for metadata in shared_presence:
                player_id = metadata.get("player_id")
                if not player_id or metadata.get("client_type") != "mobile":
                    continue
                player_breakdown.setdefault(
                    player_id,
                    {
                        "connection_count": 0,
                        "player_name": metadata.get("player_name", "Unknown"),
                    },
                )
                player_breakdown[player_id]["connection_count"] += 1

            return mobile_players, {
                "exists": True,
                "total_connections": web_clients + mobile_clients,
                "web_clients": web_clients,
                "mobile_clients": mobile_clients,
                "mobile_players": mobile_players,
                "phase": self.get_session_phase_state(session_code).get("phase"),
                "pending_acks": self.get_pending_ack_summary(session_code),
                "players": len(player_breakdown),
                "hosts": 0,
                "observers": 0,
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

        connections = self.get_session_connections(session_code)
        mobile_players = self._mobile_players_from_connections(
            session_code,
            connections,
        )
        web_clients = 0
        mobile_clients = 0
        hosts = 0
        observers = 0
        player_breakdown: Dict[str, Dict[str, Any]] = {}

        for connection_info in connections.values():
            client_type = connection_info.get("client_type", "unknown")
            player_id = connection_info.get("player_id")

            if client_type == "host":
                hosts += 1
            elif client_type == "observer":
                observers += 1
            elif client_type == "web":
                web_clients += 1
            elif client_type == "mobile":
                mobile_clients += 1
                if player_id:
                    player_breakdown.setdefault(
                        player_id,
                        {
                            "connection_count": 0,
                            "player_name": connection_info.get(
                                "player_name", "Unknown"
                            ),
                        },
                    )
                    player_breakdown[player_id]["connection_count"] += 1

        return mobile_players, {
            "exists": bool(connections),
            "total_connections": len(connections),
            "web_clients": web_clients,
            "mobile_clients": mobile_clients,
            "mobile_players": mobile_players,
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

    def get_mobile_players(self, session_code: str) -> List[Dict[str, Any]]:
        """Get list of mobile players in session"""
        shared_presence = self._shared_presence_metadata(session_code)
        if shared_presence:
            latest_by_player: Dict[str, Dict[str, Any]] = {}
            unnamed_mobile_players: List[Dict[str, Any]] = []

            for metadata in shared_presence:
                if metadata.get("client_type") != "mobile":
                    continue
                if not metadata.get("connection_confirmed"):
                    continue

                player_id = metadata.get("player_id")
                player_data = {
                    "player_id": player_id,
                    "roster_player_id": metadata.get("roster_player_id")
                    or make_roster_player_id(session_code, player_id),
                    "player_name": metadata.get("player_name")
                    or player_id
                    or "Unknown player",
                    "player_photo": metadata.get("player_photo"),
                    "connected_at": metadata.get("connected_at"),
                    "player_answered": metadata.get("player_answered", None),
                    "connection_state": metadata.get("connection_state", "connected"),
                    "is_ready": metadata.get("is_ready", False),
                }
                for key in (
                    "strike_count",
                    "max_strikes",
                    "is_frozen",
                    "frozen_question_id",
                    "is_kicked",
                    "answer_status",
                    "fair_play_reason",
                ):
                    if key in metadata:
                        player_data[key] = metadata[key]

                if player_id:
                    existing = latest_by_player.get(player_id)
                    existing_connected_at = (
                        existing.get("connected_at") if existing else ""
                    )
                    candidate_connected_at = player_data.get("connected_at") or ""
                    if not existing or candidate_connected_at >= existing_connected_at:
                        latest_by_player[player_id] = player_data
                else:
                    unnamed_mobile_players.append(player_data)

            deduped_players = list(latest_by_player.values()) + unnamed_mobile_players
            deduped_players.sort(
                key=lambda p: (p.get("player_name") or "", p.get("connected_at") or "")
            )
            return deduped_players

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
                "roster_player_id": make_roster_player_id(session_code, player_id),
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
                (player.get("player_name"), player.get("roster_player_id"))
                for player in deduped_players
            ],
        )
        return deduped_players

    def get_session_stats(self, session_code: str) -> Dict[str, Any]:
        """Get statistics for a session"""
        shared_presence = self._shared_presence_metadata(session_code)
        if shared_presence:
            mobile_players = self.get_mobile_players(session_code)
            web_clients = sum(
                1
                for metadata in shared_presence
                if metadata.get("client_type") == "web"
                and metadata.get("connection_confirmed")
            )
            mobile_clients = sum(
                1
                for metadata in shared_presence
                if metadata.get("client_type") == "mobile"
                and metadata.get("connection_confirmed")
            )
            return {
                "total_connections": web_clients + mobile_clients,
                "web_clients": web_clients,
                "mobile_clients": mobile_clients,
                "mobile_players": mobile_players,
                "phase": self.get_session_phase_state(session_code).get("phase"),
                "pending_acks": self.get_pending_ack_summary(session_code),
            }

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
                await self.send_personal_message(message, websocket)
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
        connection_info = self._connection_info_for_websocket(websocket)
        if connection_info:
            return connection_info.get("player_name") or "Unknown"
        return "Unknown"

    def get_player_connections(
        self, session_code: str, player_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get all active connections for a specific player in a session.
        Returns dict of {ws_id: connection_info}
        """
        player_key = (session_code, player_id)
        indexed_ws_ids = self.player_connection_index.get(player_key)
        if indexed_ws_ids:
            player_connections = {}
            stale_ws_ids = []
            for ws_id in indexed_ws_ids:
                conn_info = self.active_connections.get(session_code, {}).get(ws_id)
                if conn_info:
                    player_connections[ws_id] = conn_info
                else:
                    stale_ws_ids.append(ws_id)
            for ws_id in stale_ws_ids:
                indexed_ws_ids.discard(ws_id)
            if not indexed_ws_ids:
                self.player_connection_index.pop(player_key, None)
            return player_connections

        if session_code not in self.active_connections:
            return {}

        player_connections = {}
        for ws_id, conn_info in self.active_connections[session_code].items():
            if (
                conn_info.get("client_type") == "mobile"
                and conn_info.get("player_id") == player_id
            ):
                player_connections[ws_id] = conn_info
                self.player_connection_index.setdefault(player_key, set()).add(ws_id)

        return player_connections

    def disconnect_player_by_id(self, session_code: str, player_id: str) -> int:
        """
        Disconnect all connections for a specific player.
        Returns number of connections disconnected.
        """
        connections_to_remove = list(
            self.get_player_connections(session_code, player_id).items()
        )
        if not connections_to_remove:
            return 0

        disconnected_count = 0

        # Remove them
        for ws_id, conn_info in connections_to_remove:
            # Remove from session connections
            if ws_id in self.active_connections.get(session_code, {}):
                del self.active_connections[session_code][ws_id]
                disconnected_count += 1

            # Remove from registry
            self._remove_connection_indexes(session_code, ws_id, conn_info)
            self.websocket_registry.pop(ws_id, None)
            self._remove_presence(session_code, ws_id)
            self._clear_player_connection_generation(
                session_code,
                player_id,
                conn_info.get("connection_generation"),
            )

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
        shared_presence = self._shared_presence_metadata(session_code)
        if shared_presence:
            mobile_players = self.get_mobile_players(session_code)
            web_clients = sum(
                1
                for metadata in shared_presence
                if metadata.get("client_type") == "web"
                and metadata.get("connection_confirmed")
            )
            mobile_clients = sum(
                1
                for metadata in shared_presence
                if metadata.get("client_type") == "mobile"
                and metadata.get("connection_confirmed")
            )
            player_breakdown: Dict[str, Dict[str, Any]] = {}
            for metadata in shared_presence:
                player_id = metadata.get("player_id")
                if not player_id or metadata.get("client_type") != "mobile":
                    continue
                player_breakdown.setdefault(
                    player_id,
                    {
                        "connection_count": 0,
                        "player_name": metadata.get("player_name", "Unknown"),
                    },
                )
                player_breakdown[player_id]["connection_count"] += 1

            return {
                "exists": True,
                "total_connections": web_clients + mobile_clients,
                "web_clients": web_clients,
                "mobile_clients": mobile_clients,
                "mobile_players": mobile_players,
                "phase": self.get_session_phase_state(session_code).get("phase"),
                "pending_acks": self.get_pending_ack_summary(session_code),
                "players": len(player_breakdown),
                "hosts": 0,
                "observers": 0,
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
        updated_count = self._update_shared_presence_metadata(
            session_code,
            lambda metadata: metadata.get("client_type") == "mobile"
            and metadata.get("player_id") == player_id,
            lambda metadata: metadata.update({"player_answered": answered}),
        )

        for connection_info in self.active_connections.get(session_code, {}).values():
            if (
                connection_info.get("player_id") == player_id
                and connection_info.get("client_type") == "mobile"
            ):
                connection_info["player_answered"] = answered
                ws_id = connection_info.get("ws_id")
                if ws_id:
                    self._upsert_presence(session_code, ws_id, connection_info)
                updated_count += 1

        if updated_count:
            logger.debug(
                f"Set player_answered={answered} for player {player_id} in session {session_code}"
            )
            return True

        logger.warning(
            f"Player {player_id} not found in shared or local session {session_code} connections"
        )
        return False

    def reset_all_players_answered(self, session_code: str):
        """Reset the answered status for all players in a session."""
        count = self._update_shared_presence_metadata(
            session_code,
            lambda metadata: metadata.get("client_type") == "mobile",
            lambda metadata: metadata.update({"player_answered": False}),
        )

        for connection_info in self.active_connections.get(session_code, {}).values():
            if connection_info.get("client_type") == "mobile":
                connection_info["player_answered"] = False
                ws_id = connection_info.get("ws_id")
                if ws_id:
                    self._upsert_presence(session_code, ws_id, connection_info)
                count += 1

        logger.debug(
            f"Reset player_answered for {count} shared/local players in session {session_code}"
        )

    def get_player_answered_status(self, session_code: str, player_id: str) -> bool:
        """Get the answered status for a specific player"""
        shared_presence = self._shared_presence_metadata(session_code)
        if shared_presence:
            return any(
                metadata.get("client_type") == "mobile"
                and metadata.get("player_id") == player_id
                and bool(metadata.get("player_answered", False))
                for metadata in shared_presence
            )

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
        shared_presence = self._shared_presence_metadata(session_code)
        if shared_presence:
            answered_players = {
                metadata.get("player_id")
                for metadata in shared_presence
                if metadata.get("client_type") == "mobile"
                and metadata.get("player_id")
                and metadata.get("player_answered", False)
            }
            return len(answered_players)

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
        frozen_players = self.fair_play_frozen_players.get(session_code, {})
        frozen_players[player_id] = question_id
        self.fair_play_frozen_players[session_code] = frozen_players
        self._redis_hash_set(
            self._fair_play_frozen_key(session_code), player_id, question_id
        )
        self.update_fair_play_status(
            session_code,
            player_id,
            is_frozen=True,
            frozen_question_id=question_id,
        )

    def is_player_frozen_for_question(
        self, session_code: str, player_id: str, question_id: str
    ) -> bool:
        shared_frozen = self._redis_hash_get(
            self._fair_play_frozen_key(session_code), player_id
        )
        if shared_frozen is not None:
            self.fair_play_frozen_players.setdefault(session_code, {})[
                player_id
            ] = shared_frozen
            return shared_frozen == question_id

        legacy_frozen = self._redis_json_get(
            self._shared_state_key(session_code, "fair-play-frozen")
        )
        if legacy_frozen is not None:
            self.fair_play_frozen_players[session_code] = legacy_frozen
            frozen_question_id = legacy_frozen.get(player_id)
            if frozen_question_id:
                self._redis_hash_set(
                    self._fair_play_frozen_key(session_code),
                    player_id,
                    frozen_question_id,
                )
            return frozen_question_id == question_id

        return (
            self.fair_play_frozen_players.get(session_code, {}).get(player_id)
            == question_id
        )

    def clear_player_fair_play_freeze(
        self, session_code: str, player_id: str, question_id: Optional[str] = None
    ) -> None:
        frozen_question_id = self._redis_hash_get(
            self._fair_play_frozen_key(session_code), player_id
        )
        frozen_players = self.fair_play_frozen_players.get(session_code)
        if frozen_question_id is None and frozen_players:
            frozen_question_id = frozen_players.get(player_id)
        if frozen_question_id is None:
            legacy_frozen = self._redis_json_get(
                self._shared_state_key(session_code, "fair-play-frozen")
            )
            if legacy_frozen:
                self.fair_play_frozen_players[session_code] = legacy_frozen
                frozen_players = legacy_frozen
                frozen_question_id = legacy_frozen.get(player_id)
        if frozen_question_id is None:
            self.update_fair_play_status(
                session_code,
                player_id,
                is_frozen=False,
                frozen_question_id=None,
                answer_status=None,
            )
            return

        if question_id is not None and frozen_question_id != question_id:
            return

        if frozen_players:
            frozen_players.pop(player_id, None)
        self._redis_hash_delete(self._fair_play_frozen_key(session_code), player_id)
        if not frozen_players:
            self.fair_play_frozen_players.pop(session_code, None)
        else:
            self.fair_play_frozen_players[session_code] = frozen_players

        self.update_fair_play_status(
            session_code,
            player_id,
            is_frozen=False,
            frozen_question_id=None,
            answer_status=None,
        )

    def update_fair_play_status(
        self, session_code: str, player_id: str, **status: Any
    ) -> Dict[str, Any]:
        """Store host-visible Fair Play state for roster updates."""
        session_status = self.fair_play_player_status.setdefault(session_code, {})
        player_status = session_status.setdefault(player_id, {})
        shared_player_status = self._redis_hash_json_get(
            self._fair_play_status_key(session_code), player_id
        )
        if shared_player_status:
            player_status.update(shared_player_status)
        player_status.update(status)
        self.fair_play_player_status[session_code] = session_status
        self._redis_hash_json_set(
            self._fair_play_status_key(session_code),
            player_id,
            player_status,
        )
        for connection_info in self.get_player_connections(
            session_code, player_id
        ).values():
            ws_id = connection_info.get("ws_id")
            if ws_id:
                self._upsert_presence(session_code, ws_id, connection_info)
        return player_status

    def get_fair_play_status(self, session_code: str, player_id: str) -> Dict[str, Any]:
        shared_player_status = self._redis_hash_json_get(
            self._fair_play_status_key(session_code), player_id
        )
        if shared_player_status is not None:
            self.fair_play_player_status.setdefault(session_code, {})[
                player_id
            ] = shared_player_status
            return dict(shared_player_status)

        legacy_status = self._redis_json_get(
            self._shared_state_key(session_code, "fair-play-status")
        )
        if legacy_status is not None:
            self.fair_play_player_status[session_code] = legacy_status
            player_status = dict(legacy_status.get(player_id, {}))
            if player_status:
                self._redis_hash_json_set(
                    self._fair_play_status_key(session_code),
                    player_id,
                    player_status,
                )
            return player_status

        return dict(
            self.fair_play_player_status.get(session_code, {}).get(player_id, {})
        )

    def get_fair_play_statuses(self, session_code: str) -> Dict[str, Dict[str, Any]]:
        statuses: Dict[str, Dict[str, Any]] = {
            player_id: dict(status)
            for player_id, status in self.fair_play_player_status.get(
                session_code, {}
            ).items()
            if isinstance(status, dict)
        }

        legacy_status = self._redis_json_get(
            self._shared_state_key(session_code, "fair-play-status")
        )
        if isinstance(legacy_status, dict):
            for player_id, status in legacy_status.items():
                if isinstance(status, dict):
                    statuses[str(player_id)] = dict(status)

        shared_statuses = self._redis_hash_all(self._fair_play_status_key(session_code))
        for player_id, raw_status in shared_statuses.items():
            try:
                status = json.loads(raw_status)
            except (TypeError, json.JSONDecodeError):
                logger.debug(
                    "Ignoring invalid Fair Play status snapshot for %s/%s",
                    session_code,
                    safe_player_ref(str(player_id)),
                )
                continue
            if isinstance(status, dict):
                statuses[str(player_id)] = status

        if statuses:
            self.fair_play_player_status[session_code] = {
                player_id: dict(status) for player_id, status in statuses.items()
            }
        return statuses

    def reset_fair_play_freezes_for_question(
        self, session_code: str, question_id: str
    ) -> None:
        frozen_players = dict(self.fair_play_frozen_players.get(session_code, {}))
        shared_frozen_players = self._redis_hash_all(
            self._fair_play_frozen_key(session_code)
        )
        frozen_players.update(shared_frozen_players)

        if not frozen_players:
            return

        for player_id, frozen_question_id in list(frozen_players.items()):
            if frozen_question_id != question_id:
                frozen_players.pop(player_id, None)
                self._redis_hash_delete(
                    self._fair_play_frozen_key(session_code), player_id
                )
                self.update_fair_play_status(
                    session_code,
                    player_id,
                    is_frozen=False,
                    frozen_question_id=None,
                    answer_status=None,
                )

        if not frozen_players:
            self.fair_play_frozen_players.pop(session_code, None)
        else:
            self.fair_play_frozen_players[session_code] = frozen_players

    def record_pending_focus_loss(
        self,
        session_code: str,
        player_id: str,
        question_id: str,
        reason: str,
        lost_at: str,
    ) -> Dict[str, Any]:
        pending = {
            "session_code": session_code,
            "player_id": player_id,
            "question_id": question_id,
            "reason": reason,
            "lost_at": lost_at,
        }
        session_pending = self.pending_focus_losses.setdefault(session_code, {})
        session_pending[player_id] = pending
        self.pending_focus_losses[session_code] = session_pending
        self._redis_hash_json_set(
            self._pending_focus_key(session_code),
            player_id,
            pending,
        )
        return pending

    def get_pending_focus_loss(
        self, session_code: str, player_id: str
    ) -> Optional[Dict[str, Any]]:
        shared_pending = self._redis_hash_json_get(
            self._pending_focus_key(session_code), player_id
        )
        if shared_pending is not None:
            self.pending_focus_losses.setdefault(session_code, {})[
                player_id
            ] = shared_pending
            return dict(shared_pending)

        legacy_pending = self._redis_json_get(
            self._shared_state_key(session_code, "pending-focus")
        )
        if legacy_pending is not None:
            self.pending_focus_losses[session_code] = legacy_pending
            pending = legacy_pending.get(player_id)
            if pending:
                self._redis_hash_json_set(
                    self._pending_focus_key(session_code), player_id, pending
                )
            return dict(pending) if pending else None

        pending = self.pending_focus_losses.get(session_code, {}).get(player_id)
        return dict(pending) if pending else None

    def clear_pending_focus_loss(
        self, session_code: str, player_id: str
    ) -> Optional[Dict[str, Any]]:
        pending = self._redis_hash_json_get(
            self._pending_focus_key(session_code), player_id
        )
        session_pending = self.pending_focus_losses.get(session_code)
        if pending is None and session_pending:
            pending = session_pending.get(player_id)
        if pending is None:
            legacy_pending = self._redis_json_get(
                self._shared_state_key(session_code, "pending-focus")
            )
            if legacy_pending:
                self.pending_focus_losses[session_code] = legacy_pending
                session_pending = legacy_pending
                pending = legacy_pending.get(player_id)
        if pending is None:
            return None

        if session_pending:
            session_pending.pop(player_id, None)
        self._redis_hash_delete(self._pending_focus_key(session_code), player_id)
        if not session_pending:
            self.pending_focus_losses.pop(session_code, None)
        else:
            self.pending_focus_losses[session_code] = session_pending
        return pending

    def update_heartbeat(self, websocket: WebSocket):
        """Update the last heartbeat time for a websocket"""
        ws_id = self._ws_id_for_websocket(websocket)
        if not ws_id:
            return

        session_code = self.websocket_registry.get(ws_id, {}).get("session_code")
        connection_info = self.active_connections.get(session_code, {}).get(ws_id)
        if not connection_info:
            return

        connection_info["last_heartbeat"] = datetime.now()
        connection_info["connection_state"] = "connected"
        self._upsert_presence(session_code, ws_id, connection_info)
        if (
            connection_info.get("client_type") == "mobile"
            and connection_info.get("player_id")
            and connection_info.get("connection_generation")
        ):
            now = time.time()
            last_renewed_at = float(connection_info.get("generation_renewed_at") or 0)
            if now - last_renewed_at >= self.GENERATION_RENEW_INTERVAL_SECONDS:
                connection_info["generation_renewed_at"] = now
                try:
                    asyncio.create_task(
                        self._renew_or_disconnect_generation(
                            session_code,
                            connection_info["player_id"],
                            connection_info.get("connection_generation"),
                        )
                    )
                except RuntimeError:
                    logger.debug("Could not schedule generation renewal; no event loop")

    def mark_client_ready(self, websocket: WebSocket):
        """Mark a client as ready after they acknowledge connection"""
        ws_id = self._ws_id_for_websocket(websocket)
        if not ws_id:
            return

        session_code = self.websocket_registry.get(ws_id, {}).get("session_code")
        connection_info = self.active_connections.get(session_code, {}).get(ws_id)
        if not connection_info:
            return

        connection_info["is_ready"] = True
        self._upsert_presence(session_code, ws_id, connection_info)
        logger.info(f"Client {ws_id} marked as ready")

    async def broadcast_player_roster_update(self, session_code: str):
        """Broadcast the authoritative mobile player roster to host clients."""
        mobile_players, stats = self.build_roster_snapshot(session_code)

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

    async def schedule_player_roster_update(
        self, session_code: str, delay_seconds: Optional[float] = None
    ) -> None:
        """Debounce roster rebuild/broadcast work for join and reconnect bursts."""
        existing_task = self.roster_update_tasks.get(session_code)
        if existing_task and not existing_task.done():
            return

        delay = (
            self.ROSTER_UPDATE_DEBOUNCE_SECONDS
            if delay_seconds is None
            else delay_seconds
        )

        async def delayed_roster_update() -> None:
            try:
                await asyncio.sleep(delay)
                await self.broadcast_player_roster_update(session_code)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to broadcast debounced roster update for %s",
                    session_code,
                )
            finally:
                current_task = asyncio.current_task()
                if self.roster_update_tasks.get(session_code) is current_task:
                    self.roster_update_tasks.pop(session_code, None)

        self.roster_update_tasks[session_code] = asyncio.create_task(
            delayed_roster_update()
        )

    async def wait_for_ready_connections(self, session_code: str, timeout: float = 2.0):
        """Wait for all connections to be ready before proceeding with critical broadcasts"""
        start_time = datetime.now()

        while (datetime.now() - start_time).total_seconds() < timeout:
            shared_presence = self._shared_presence_metadata(session_code)
            if shared_presence:
                connections = {
                    metadata.get("member") or metadata.get("ws_id"): metadata
                    for metadata in shared_presence
                    if metadata.get("connection_confirmed")
                }
            else:
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
                        "serverTime": self._utc_now_ms(),
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
            queued = {
                "question_data": question_data,
                "queued_at": datetime.now().isoformat(),
            }
            self.question_queue[session_code][question_id] = queued
            self._redis_json_set(
                self._shared_state_key(session_code, "current-question"),
                queued,
            )
            logger.info(f"📥 Queued question {question_id} for session {session_code}")

    def get_current_question(self, session_code: str) -> Optional[Dict[str, Any]]:
        """
        Get the most recently queued question for a session.
        Returns None if no questions are queued.
        """
        shared_question = self._redis_json_get(
            self._shared_state_key(session_code, "current-question")
        )
        if shared_question and shared_question.get("question_data"):
            return shared_question["question_data"]

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
            logger.info("Cleared question queue for session %s", session_code)
        self._redis_delete(self._shared_state_key(session_code, "current-question"))

    def get_buzzer_state(self, session_code: str) -> Dict[str, Any]:
        """Return shared per-session buzzer state."""
        shared_state = self._redis_json_get(
            self._shared_state_key(session_code, "buzzer")
        )
        if shared_state:
            state = self._deserialize_buzzer_state(shared_state)
            self.buzzer_states[session_code] = state
            return state

        state = self.buzzer_states.setdefault(
            session_code,
            {
                "current_buzzer_winner": None,
                "frozen_players": set(),
                "question_active": False,
                "transitioning": False,
                "accepting_buzzes": False,
                "current_question_id": None,
                "attempts": [],
            },
        )
        self.save_buzzer_state(session_code, state)
        return state

    def start_buzzer_question(self, session_code: str, question_id: Optional[str]):
        """Mark a buzzer question active for all connections in the session."""
        state = self.get_buzzer_state(session_code)
        state.update(
            {
                "current_buzzer_winner": None,
                "frozen_players": set(),
                "question_active": True,
                "transitioning": False,
                "accepting_buzzes": True,
                "current_question_id": question_id,
                "attempts": [],
            }
        )
        self.save_buzzer_state(session_code, state)
        logger.info(f"Buzzer question active for session {session_code}: {question_id}")
        return state

    def reset_buzzer_state(self, session_code: str):
        """Reset shared buzzer state for a session."""
        self.buzzer_states[session_code] = {
            "current_buzzer_winner": None,
            "frozen_players": set(),
            "question_active": False,
            "transitioning": False,
            "accepting_buzzes": False,
            "current_question_id": None,
            "attempts": [],
        }
        self.save_buzzer_state(session_code, self.buzzer_states[session_code])
        logger.info(f"Reset buzzer state for session {session_code}")

    def lock_buzzer_until_next_question(self, session_code: str):
        """Lock buzzers while the old question is ending and the next is pending."""
        state = self.get_buzzer_state(session_code)
        state.update(
            {
                "current_buzzer_winner": None,
                "question_active": False,
                "transitioning": True,
                "accepting_buzzes": False,
            }
        )
        self.save_buzzer_state(session_code, state)
        logger.info(f"Locked buzzer state during transition for session {session_code}")
        return state

    def format_buzzer_state_update(self, session_code: str) -> Dict[str, Any]:
        state = self.get_buzzer_state(session_code)
        current_winner = state.get("current_buzzer_winner")
        frozen_players = list(state.get("frozen_players", set()))
        return {
            "question_id": state.get("current_question_id"),
            "current_buzzer_winner": current_winner,
            "current_buzzer_winner_roster_id": (
                make_roster_player_id(session_code, current_winner)
                if current_winner
                else None
            ),
            "frozen_players": frozen_players,
            "frozen_roster_player_ids": [
                make_roster_player_id(session_code, player_id)
                for player_id in frozen_players
            ],
            "question_active": state.get("question_active", False),
            "transitioning": state.get("transitioning", False),
            "accepting_buzzes": state.get("accepting_buzzes", False),
            "button_state": (
                "active"
                if state.get("question_active")
                and state.get("accepting_buzzes")
                and not state.get("transitioning")
                and not current_winner
                else "waiting"
            ),
            "server_time_ms": self._utc_now_ms(),
        }

    async def broadcast_buzzer_state_update(self, session_code: str) -> None:
        await self.broadcast_to_session(
            session_code,
            {
                "type": "buzzer_state_update",
                "data": self.format_buzzer_state_update(session_code),
            },
            only_client_types=["mobile"],
            critical=True,
            require_ack=True,
        )

    def set_session_game_type(self, session_code: str, game_type: str):
        """Store the resolved game type for scheduler and reconnect paths."""
        self.session_game_types[session_code] = game_type
        self._redis_json_set(
            self._shared_state_key(session_code, "game-type"),
            {"session_code": session_code, "game_type": game_type},
        )
        logger.info(f"Session {session_code} game type set to {game_type}")

    def get_session_game_type(self, session_code: str) -> Optional[str]:
        """Return the resolved game type for a session if known."""
        shared_game_type = self._redis_json_get(
            self._shared_state_key(session_code, "game-type")
        )
        if shared_game_type and shared_game_type.get("game_type"):
            game_type = str(shared_game_type["game_type"])
            self.session_game_types[session_code] = game_type
            return game_type

        return self.session_game_types.get(session_code)

    def set_beat_clock_state(self, session_code: str, state: Dict[str, Any]) -> None:
        self.beat_clock_states[session_code] = state
        meta_key, players_key = self._beat_clock_keys(session_code)
        meta_state = {key: value for key, value in state.items() if key != "players"}
        self._redis_json_set(meta_key, self._json_safe_state(meta_state))
        client = websocket_bus.sync_client
        if client:
            try:
                client.expire(players_key, self.SHARED_STATE_TTL_SECONDS)
            except Exception:
                logger.exception(
                    "Failed to refresh Beat the Clock player state TTL for %s",
                    session_code,
                )

    def get_beat_clock_state(self, session_code: str) -> Dict[str, Any]:
        meta_key, players_key = self._beat_clock_keys(session_code)
        shared_state = self._redis_json_get(meta_key)
        client = websocket_bus.sync_client

        if shared_state is None:
            legacy_state = self._redis_json_get(
                self._shared_state_key(session_code, "beat-clock")
            )
            if legacy_state is not None:
                shared_state = {
                    key: value
                    for key, value in legacy_state.items()
                    if key != "players"
                }
                self._redis_json_set(meta_key, self._json_safe_state(shared_state))
                if client and legacy_state.get("players"):
                    try:
                        pipe = client.pipeline()
                        for player_id, player_state in legacy_state["players"].items():
                            pipe.hset(
                                players_key,
                                player_id,
                                json.dumps(
                                    self._json_safe_state(player_state),
                                    separators=(",", ":"),
                                ),
                            )
                        pipe.expire(players_key, self.SHARED_STATE_TTL_SECONDS)
                        pipe.execute()
                    except Exception:
                        logger.exception(
                            "Failed to migrate legacy Beat the Clock player state for %s",
                            session_code,
                        )

        if shared_state is not None:
            players: Dict[str, Any] = {}
            if client:
                try:
                    for player_id, raw_value in client.hgetall(players_key).items():
                        try:
                            players[player_id] = json.loads(raw_value)
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    logger.exception(
                        "Failed to read Beat the Clock player states for %s",
                        session_code,
                    )

            local_state = self.beat_clock_states.get(session_code, {})
            merged_state = {**local_state, **shared_state}
            for key, value in local_state.items():
                if key in {"ends_at_dt", "started_at_dt"} and key not in merged_state:
                    merged_state[key] = value
            ends_at_raw = merged_state.get("ends_at")
            if ends_at_raw and not merged_state.get("ends_at_dt"):
                try:
                    merged_state["ends_at_dt"] = datetime.fromisoformat(
                        str(ends_at_raw).replace("Z", "")
                    )
                except ValueError:
                    pass
            merged_state["players"] = players
            self.beat_clock_states[session_code] = merged_state
            return merged_state

        state = self.beat_clock_states.setdefault(
            session_code,
            {
                "active": False,
                "players": {},
                "questions": [],
                "leaderboard": [],
            },
        )
        self.set_beat_clock_state(session_code, state)
        return state

    def get_beat_clock_state_for_player(
        self, session_code: str, player_id: str
    ) -> Dict[str, Any]:
        """Return Beat the Clock meta plus one player's state without HGETALL."""
        meta_key, players_key = self._beat_clock_keys(session_code)
        shared_state = self._redis_json_get(meta_key)
        client = websocket_bus.sync_client
        player_state = None

        if shared_state is None:
            legacy_state = self._redis_json_get(
                self._shared_state_key(session_code, "beat-clock")
            )
            if legacy_state is not None:
                shared_state = {
                    key: value
                    for key, value in legacy_state.items()
                    if key != "players"
                }
                self._redis_json_set(meta_key, self._json_safe_state(shared_state))
                player_state = (legacy_state.get("players") or {}).get(player_id)
                if client and player_state is not None:
                    try:
                        pipe = client.pipeline()
                        pipe.hset(
                            players_key,
                            player_id,
                            json.dumps(
                                self._json_safe_state(player_state),
                                separators=(",", ":"),
                            ),
                        )
                        pipe.expire(players_key, self.SHARED_STATE_TTL_SECONDS)
                        pipe.execute()
                    except Exception:
                        logger.exception(
                            "Failed to migrate Beat the Clock player state for %s/%s",
                            session_code,
                            safe_player_ref(player_id),
                        )

        if shared_state is not None:
            if client and player_state is None:
                try:
                    raw_value = client.hget(players_key, player_id)
                    if raw_value:
                        player_state = json.loads(raw_value)
                except Exception:
                    logger.exception(
                        "Failed to read Beat the Clock player state for %s/%s",
                        session_code,
                        safe_player_ref(player_id),
                    )

            local_state = self.beat_clock_states.get(session_code, {})
            merged_state = {**local_state, **shared_state}
            ends_at_raw = merged_state.get("ends_at")
            if ends_at_raw and not merged_state.get("ends_at_dt"):
                try:
                    merged_state["ends_at_dt"] = datetime.fromisoformat(
                        str(ends_at_raw).replace("Z", "")
                    )
                except ValueError:
                    pass

            players = dict(local_state.get("players") or {})
            if player_state is not None:
                players[player_id] = player_state
            merged_state["players"] = players
            self.beat_clock_states[session_code] = merged_state
            return merged_state

        state = self.beat_clock_states.setdefault(
            session_code,
            {
                "active": False,
                "players": {},
                "questions": [],
                "leaderboard": [],
            },
        )
        self.set_beat_clock_state(session_code, state)
        return state

    def update_beat_clock_player_state(
        self, session_code: str, player_id: str, player_state: Dict[str, Any]
    ) -> None:
        state = self.beat_clock_states.setdefault(session_code, {})
        state.setdefault("players", {})[player_id] = player_state
        client = websocket_bus.sync_client
        if not client:
            return

        _meta_key, players_key = self._beat_clock_keys(session_code)
        try:
            pipe = client.pipeline()
            pipe.hset(
                players_key,
                player_id,
                json.dumps(
                    self._json_safe_state(player_state),
                    separators=(",", ":"),
                ),
            )
            pipe.expire(players_key, self.SHARED_STATE_TTL_SECONDS)
            pipe.execute()
        except Exception:
            logger.exception(
                "Failed to update Beat the Clock player state for %s/%s",
                session_code,
                safe_player_ref(player_id),
            )

    def claim_beat_clock_finish(
        self, session_code: str, ttl_seconds: int = 900
    ) -> bool:
        client = websocket_bus.sync_client
        if client:
            try:
                return bool(
                    client.set(
                        self._beat_clock_finish_key(session_code),
                        self._utc_now_iso(),
                        nx=True,
                        ex=ttl_seconds,
                    )
                )
            except Exception:
                logger.exception("Failed to claim Beat the Clock finish lock")
                return False

        state = self.beat_clock_states.setdefault(session_code, {})
        if state.get("ending"):
            return False
        state["ending"] = True
        return True

    def clear_beat_clock_state(self, session_code: str) -> None:
        self.beat_clock_states.pop(session_code, None)
        meta_key, players_key = self._beat_clock_keys(session_code)
        self._redis_delete(
            self._shared_state_key(session_code, "beat-clock"),
            meta_key,
            players_key,
            self._beat_clock_finish_key(session_code),
        )


# Global connection manager instance
manager = ConnectionManager()
