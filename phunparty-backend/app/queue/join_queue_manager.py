import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional, Set
from dataclasses import dataclass
from enum import Enum
import logging

from app.database.dbCRUD import get_session_by_code, join_game
from app.websockets.manager import manager as websocket_manager

logger = logging.getLogger(__name__)


class QueueStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class QueueEntry:
    """Represents a single join request in the queue"""

    queue_id: str
    player_id: str
    session_code: str
    websocket_id: Optional[str]
    status: QueueStatus
    created_at: datetime
    attempts: int = 0
    max_attempts: int = 3
    error_message: Optional[str] = None


class JoinQueueManager:
    """
    Manages a queue of join requests to prevent race conditions when multiple players
    try to join the same session simultaneously. Processes requests sequentially
    with WebSocket notifications for real-time updates.
    """

    def __init__(self):
        self.queue: Dict[str, QueueEntry] = {}
        self.processing_sessions: Set[str] = set()
        self.queue_timeout = 30  # seconds
        self.cleanup_interval = 60  # seconds
        self._processor_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Start the queue processor and cleanup tasks"""
        if self._running:
            return

        self._running = True
        self._processor_task = asyncio.create_task(self._process_queue())
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_entries())
        logger.info("JoinQueueManager started")

    async def stop(self):
        """Stop the queue processor and cleanup tasks"""
        self._running = False

        # Stop processor task
        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await asyncio.wait_for(self._processor_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Stop cleanup task
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        logger.info("JoinQueueManager stopped")

    async def add_to_queue(
        self, player_id: str, session_code: str, websocket_id: Optional[str] = None
    ) -> str:
        """
        Add a join request to the queue

        Args:
            player_id: ID of the player trying to join
            session_code: Code of the session to join
            websocket_id: Optional WebSocket connection ID for notifications

        Returns:
            queue_id: Unique identifier for tracking this queue entry
        """
        queue_id = str(uuid.uuid4())

        entry = QueueEntry(
            queue_id=queue_id,
            player_id=player_id,
            session_code=session_code.upper(),
            websocket_id=websocket_id,
            status=QueueStatus.PENDING,
            created_at=datetime.utcnow(),
        )

        self.queue[queue_id] = entry

        # Notify via WebSocket if connection available
        if websocket_id:
            await self._notify_websocket(
                websocket_id,
                {
                    "type": "queue_status",
                    "queue_id": queue_id,
                    "status": "pending",
                    "message": "Added to join queue",
                    "position": await self._get_queue_position(queue_id),
                },
            )

        logger.info(f"Added player {player_id} to queue for session {session_code}")
        return queue_id

    async def get_queue_status(self, queue_id: str) -> Optional[Dict]:
        """Get the current status of a queue entry"""
        entry = self.queue.get(queue_id)
        if not entry:
            return None

        return {
            "queue_id": queue_id,
            "status": entry.status.value,
            "created_at": entry.created_at.isoformat(),
            "attempts": entry.attempts,
            "error_message": entry.error_message,
            "position": (
                await self._get_queue_position(queue_id)
                if entry.status == QueueStatus.PENDING
                else None
            ),
        }

    async def _process_queue(self):
        """Main queue processing loop"""
        while self._running:
            try:
                # Get next pending entry
                pending_entries = [
                    entry
                    for entry in self.queue.values()
                    if entry.status == QueueStatus.PENDING
                ]

                if not pending_entries:
                    await asyncio.sleep(0.1)
                    continue

                # Sort by creation time to process in FIFO order
                pending_entries.sort(key=lambda x: x.created_at)
                entry = pending_entries[0]

                # Skip if session is already being processed
                if entry.session_code in self.processing_sessions:
                    await asyncio.sleep(0.1)
                    continue

                await self._process_entry(entry)

            except Exception as e:
                logger.error(f"Error in queue processor: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _process_entry(self, entry: QueueEntry):
        """Process a single queue entry"""
        try:
            # Mark session as being processed
            self.processing_sessions.add(entry.session_code)
            entry.status = QueueStatus.PROCESSING
            entry.attempts += 1

            # Notify WebSocket of processing status
            if entry.websocket_id:
                await self._notify_websocket(
                    entry.websocket_id,
                    {
                        "type": "queue_status",
                        "queue_id": entry.queue_id,
                        "status": "processing",
                        "message": "Processing join request...",
                    },
                )

            # Attempt to join the session
            result = await self._attempt_join(entry.player_id, entry.session_code)

            if result["success"]:
                entry.status = QueueStatus.SUCCESS
                await self._notify_success(entry, result)
            else:
                entry.error_message = result["message"]

                if entry.attempts >= entry.max_attempts:
                    entry.status = QueueStatus.FAILED
                    await self._notify_failure(entry, result["message"])
                else:
                    entry.status = QueueStatus.PENDING
                    await self._notify_retry(entry, result["message"])

        except Exception as e:
            entry.error_message = f"Processing error: {str(e)}"
            entry.status = QueueStatus.FAILED
            await self._notify_failure(entry, entry.error_message)
            logger.error(
                f"Error processing queue entry {entry.queue_id}: {e}", exc_info=True
            )

        finally:
            # Remove session from processing set
            self.processing_sessions.discard(entry.session_code)

    async def _attempt_join(self, player_id: str, session_code: str) -> Dict:
        """
        Attempt to join a player to a session

        Returns:
            Dict with 'success' boolean and 'message' string
        """
        try:
            from app.dependencies import get_db
            from sqlalchemy.orm import Session

            # Get database session - this is a workaround since we don't have async db functions
            # In a production environment, consider using async database operations
            db_gen = get_db()
            db: Session = next(db_gen)

            try:
                # Use the existing join_game function which handles all the logic
                result = join_game(db, session_code, int(player_id))

                return {
                    "success": True,
                    "message": "Successfully joined session",
                    "session_data": {
                        "session_code": result.session_code,
                        "host_name": result.host_name,
                        "game_code": result.game_code,
                        "number_of_questions": result.number_of_questions,
                    },
                }

            except ValueError as e:
                # Handle specific business logic errors from join_game
                return {"success": False, "message": str(e)}
            except Exception as e:
                logger.error(f"Unexpected error in join_game: {e}", exc_info=True)
                return {"success": False, "message": f"Internal error: {str(e)}"}
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error in _attempt_join: {e}", exc_info=True)
            return {"success": False, "message": f"Internal error: {str(e)}"}

    async def _notify_websocket(self, websocket_id: str, message: Dict):
        """Send notification via WebSocket"""
        try:
            await websocket_manager.send_personal_message_by_id(message, websocket_id)
        except Exception as e:
            logger.error(f"Failed to send WebSocket notification: {e}")

    async def _notify_success(self, entry: QueueEntry, result: Dict):
        """Notify successful join"""
        if entry.websocket_id:
            await self._notify_websocket(
                entry.websocket_id,
                {
                    "type": "join_success",
                    "queue_id": entry.queue_id,
                    "session_code": entry.session_code,
                    "message": "Successfully joined session!",
                    "session_data": result.get("session_data"),
                },
            )

        # Clean up entry after success
        self.queue.pop(entry.queue_id, None)

    async def _notify_failure(self, entry: QueueEntry, error_message: str):
        """Notify failed join"""
        if entry.websocket_id:
            await self._notify_websocket(
                entry.websocket_id,
                {
                    "type": "join_failed",
                    "queue_id": entry.queue_id,
                    "session_code": entry.session_code,
                    "message": error_message,
                    "attempts": entry.attempts,
                    "max_attempts": entry.max_attempts,
                },
            )

    async def _notify_retry(self, entry: QueueEntry, error_message: str):
        """Notify retry attempt"""
        if entry.websocket_id:
            await self._notify_websocket(
                entry.websocket_id,
                {
                    "type": "queue_retry",
                    "queue_id": entry.queue_id,
                    "session_code": entry.session_code,
                    "message": f"Retrying... ({entry.attempts}/{entry.max_attempts})",
                    "error": error_message,
                    "position": await self._get_queue_position(entry.queue_id),
                },
            )

    async def _get_queue_position(self, queue_id: str) -> int:
        """Get position in queue (1-based)"""
        pending_entries = [
            entry
            for entry in self.queue.values()
            if entry.status == QueueStatus.PENDING
        ]
        pending_entries.sort(key=lambda x: x.created_at)

        for i, entry in enumerate(pending_entries):
            if entry.queue_id == queue_id:
                return i + 1

        return 0

    async def _cleanup_expired_entries(self):
        """Clean up expired queue entries"""
        while self._running:
            try:
                current_time = datetime.utcnow()
                expired_entries = []

                for queue_id, entry in self.queue.items():
                    age = current_time - entry.created_at

                    # Mark as timeout if expired and still pending/processing
                    if age.total_seconds() > self.queue_timeout and entry.status in [
                        QueueStatus.PENDING,
                        QueueStatus.PROCESSING,
                    ]:
                        entry.status = QueueStatus.TIMEOUT
                        entry.error_message = "Queue request timed out"

                        if entry.websocket_id:
                            await self._notify_websocket(
                                entry.websocket_id,
                                {
                                    "type": "join_timeout",
                                    "queue_id": queue_id,
                                    "message": "Join request timed out",
                                },
                            )

                    # Clean up completed/failed/timeout entries after additional time
                    if (
                        age.total_seconds() > self.queue_timeout * 2
                        and entry.status
                        in [
                            QueueStatus.SUCCESS,
                            QueueStatus.FAILED,
                            QueueStatus.TIMEOUT,
                        ]
                    ):
                        expired_entries.append(queue_id)

                # Remove expired entries
                for queue_id in expired_entries:
                    self.queue.pop(queue_id, None)

                if expired_entries:
                    logger.info(
                        f"Cleaned up {len(expired_entries)} expired queue entries"
                    )

                await asyncio.sleep(self.cleanup_interval)

            except Exception as e:
                logger.error(f"Error in cleanup task: {e}", exc_info=True)
                await asyncio.sleep(self.cleanup_interval)

    def get_queue_stats(self) -> Dict:
        """Get current queue statistics"""
        stats = {
            "total_entries": len(self.queue),
            "pending": len(
                [e for e in self.queue.values() if e.status == QueueStatus.PENDING]
            ),
            "processing": len(
                [e for e in self.queue.values() if e.status == QueueStatus.PROCESSING]
            ),
            "success": len(
                [e for e in self.queue.values() if e.status == QueueStatus.SUCCESS]
            ),
            "failed": len(
                [e for e in self.queue.values() if e.status == QueueStatus.FAILED]
            ),
            "timeout": len(
                [e for e in self.queue.values() if e.status == QueueStatus.TIMEOUT]
            ),
            "processing_sessions": list(self.processing_sessions),
            "is_running": self._running,
        }
        return stats


# Global queue manager instance
join_queue_manager = JoinQueueManager()
