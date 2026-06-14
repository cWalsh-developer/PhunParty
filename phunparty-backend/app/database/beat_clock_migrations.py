import logging

from app.config import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def ensure_beat_clock_session_columns() -> None:
    """Add Beat the Clock settings for existing session tables."""
    statements = [
        """
        ALTER TABLE game_sessions
        ADD COLUMN IF NOT EXISTS beat_clock_duration_seconds
        INTEGER NOT NULL DEFAULT 60
        """,
        """
        ALTER TABLE game_sessions
        ALTER COLUMN beat_clock_duration_seconds SET DEFAULT 60
        """,
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

    logger.info("Beat the Clock session columns are ready")
