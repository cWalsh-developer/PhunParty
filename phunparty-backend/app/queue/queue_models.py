from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class JoinQueueRequest(BaseModel):
    """Request model for joining a session via queue"""

    player_id: str = Field(..., description="ID of the player trying to join")
    session_code: str = Field(
        ..., description="Code of the session to join", min_length=4, max_length=10
    )
    websocket_id: Optional[str] = Field(
        None, description="Optional WebSocket connection ID for real-time updates"
    )


class JoinQueueResponse(BaseModel):
    """Response model for queue join request"""

    success: bool
    message: str
    queue_id: Optional[str] = Field(
        None, description="Unique identifier for tracking the queue entry"
    )
    estimated_wait_time: Optional[int] = Field(
        None, description="Estimated wait time in seconds"
    )


class QueueStatusResponse(BaseModel):
    """Response model for queue status inquiry"""

    success: bool
    message: str
    queue_id: Optional[str] = None
    status: Optional[str] = Field(
        None,
        description="Current queue status: pending, processing, success, failed, timeout",
    )
    created_at: Optional[datetime] = None
    attempts: Optional[int] = None
    max_attempts: Optional[int] = None
    error_message: Optional[str] = None
    position: Optional[int] = Field(
        None, description="Position in queue (1-based, null if not pending)"
    )


class QueueStatsResponse(BaseModel):
    """Response model for queue statistics"""

    success: bool
    message: str
    stats: Optional[dict] = Field(
        None, description="Queue statistics including counts by status"
    )


class WebSocketQueueMessage(BaseModel):
    """WebSocket message model for queue-related notifications"""

    type: str = Field(
        ...,
        description="Message type: queue_status, join_success, join_failed, join_timeout, queue_retry",
    )
    queue_id: str = Field(..., description="Queue entry identifier")
    status: Optional[str] = Field(None, description="Current queue status")
    message: str = Field(..., description="Human-readable message")
    session_code: Optional[str] = Field(None, description="Session code being joined")
    position: Optional[int] = Field(None, description="Position in queue")
    attempts: Optional[int] = Field(None, description="Current attempt number")
    max_attempts: Optional[int] = Field(None, description="Maximum allowed attempts")
    error: Optional[str] = Field(None, description="Error message for retries")
    session_data: Optional[dict] = Field(
        None, description="Session data on successful join"
    )
