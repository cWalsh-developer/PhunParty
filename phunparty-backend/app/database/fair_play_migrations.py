import logging

from app.config import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def ensure_fair_play_columns() -> None:
    """Add Fair Play setting columns for existing session-state tables."""
    statements = [
        """
        ALTER TABLE game_session_states
        ADD COLUMN IF NOT EXISTS fair_play_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE game_session_states
        ADD COLUMN IF NOT EXISTS max_fair_play_strikes INTEGER NOT NULL DEFAULT 3
        """,
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

    logger.info("Fair Play session columns are ready")
