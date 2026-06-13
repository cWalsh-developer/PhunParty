import logging

from app.config import SessionLocal, engine
from app.database.dbCRUD import generate_unique_friend_code
from app.schemas.players_model import Players
from sqlalchemy import text

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
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS profile_visibility VARCHAR NOT NULL DEFAULT 'public'
        """,
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS show_online_status BOOLEAN NOT NULL DEFAULT TRUE
        """,
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS allow_friend_requests BOOLEAN NOT NULL DEFAULT TRUE
        """,
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS share_game_stats BOOLEAN NOT NULL DEFAULT TRUE
        """,
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS data_collection BOOLEAN NOT NULL DEFAULT TRUE
        """,
        """
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS crash_reporting BOOLEAN NOT NULL DEFAULT TRUE
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
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'players_profile_visibility_check'
                    ) THEN
                        ALTER TABLE players
                        ADD CONSTRAINT players_profile_visibility_check
                        CHECK (profile_visibility IN ('public', 'friends', 'private'));
                    END IF;
                END $$;
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS player_presence (
                    player_id VARCHAR PRIMARY KEY
                        REFERENCES players(player_id) ON DELETE CASCADE,
                    is_online BOOLEAN NOT NULL DEFAULT FALSE,
                    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_player_presence_is_online
                ON player_presence(is_online)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_player_presence_last_seen_at
                ON player_presence(last_seen_at)
                """
            )
        )

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ix_players_active_mobile_unique
                    ON players ((
                        CASE
                            WHEN player_mobile LIKE '0%'
                            THEN '+44' || substring(player_mobile from 2)
                            ELSE player_mobile
                        END
                    ))
                    WHERE player_mobile IS NOT NULL
                    AND is_deleted = FALSE
                    """
                )
            )
    except Exception as exc:
        logger.warning(
            "Could not create unique active phone index. Resolve duplicate active "
            "player_mobile values, then rerun startup migration: %s",
            exc,
        )

    logger.info("Social player columns are ready")
