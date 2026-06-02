import logging

from sqlalchemy import text

from app.config import SessionLocal, engine
from app.database.dbCRUD import generate_unique_friend_code
from app.schemas.players_model import Players

logger = logging.getLogger(__name__)


def ensure_social_player_columns() -> None:
    """Add social profile columns for databases created before this feature."""
    column_statements = [
        "ALTER TABLE players ADD COLUMN IF NOT EXISTS friend_code VARCHAR",
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS allow_friend_code_search BOOLEAN NOT NULL DEFAULT TRUE
        """,
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS allow_phone_discovery BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS friend_request_notifications_enabled
        BOOLEAN NOT NULL DEFAULT TRUE
        """,
    ]

    with engine.begin() as connection:
        for statement in column_statements:
            connection.execute(text(statement))

    with SessionLocal() as db:
        players_without_codes = (
            db.query(Players).filter(Players.friend_code.is_(None)).all()
        )
        for player in players_without_codes:
            player.friend_code = generate_unique_friend_code(db)
        if players_without_codes:
            db.commit()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ix_players_friend_code
                ON players(friend_code)
                """
            )
        )
        connection.execute(
            text("ALTER TABLE players ALTER COLUMN friend_code SET NOT NULL")
        )

    logger.info("Social player columns are ready")
