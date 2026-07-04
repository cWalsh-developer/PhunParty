import logging

from app.config import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def ensure_email_verification_columns() -> None:
    """Add email verification fields for databases created before this feature."""
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE players ADD COLUMN IF NOT EXISTS email_verified BOOLEAN")
        )
        connection.execute(
            text(
                """
                UPDATE players
                SET email_verified = TRUE
                WHERE email_verified IS NULL
                """
            )
        )
        connection.execute(
            text("ALTER TABLE players ALTER COLUMN email_verified SET DEFAULT FALSE")
        )
        connection.execute(
            text("ALTER TABLE players ALTER COLUMN email_verified SET NOT NULL")
        )
        connection.execute(
            text(
                """
                ALTER TABLE players
                ADD COLUMN IF NOT EXISTS email_verification_code_hash VARCHAR
                """
            )
        )
        connection.execute(
            text(
                """
                ALTER TABLE players
                ADD COLUMN IF NOT EXISTS email_verification_expires_at TIMESTAMP
                """
            )
        )

    logger.info("Email verification player columns are ready")
