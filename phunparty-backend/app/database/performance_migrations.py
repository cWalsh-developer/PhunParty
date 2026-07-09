import logging

from app.config import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


POSTGRES_INDEXES = [
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_questions_lower_genre_difficulty
    ON questions (lower(genre), difficulty)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_friend_requests_receiver_status_created
    ON friend_requests (receiver_player_id, status, created_at DESC)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_friend_requests_sender_status_created
    ON friend_requests (sender_player_id, status, created_at DESC)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_friend_requests_pair_status
    ON friend_requests (sender_player_id, receiver_player_id, status)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_friendships_high_low
    ON friendships (player_high_id, player_low_id)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_recipient_read_created
    ON notifications (recipient_player_id, is_read, created_at DESC)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_push_tokens_player_active
    ON user_push_tokens (player_id)
    WHERE is_active = TRUE
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_player_presence_online_seen
    ON player_presence (is_online, last_seen_at DESC)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scores_session_player
    ON scores (session_code, player_id)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scores_session_score
    ON scores (session_code, score DESC)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scores_player_result
    ON scores (player_id, result)
    WHERE result IS NOT NULL
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_session_player_assignments_active_session
    ON session_player_assignments (session_code, player_id)
    WHERE session_end IS NULL
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_session_player_assignments_player_history
    ON session_player_assignments (player_id, session_start DESC)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_session_question_assignments_session_question
    ON session_question_assignments (session_code, question_id)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_game_session_states_active_public_session
    ON game_session_states (is_active, ispublic, session_code)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_game_session_states_session_active
    ON game_session_states (session_code)
    WHERE is_active = TRUE
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_players_friend_code_search
    ON players (friend_code)
    WHERE allow_friend_code_search = TRUE
      AND is_deleted = FALSE
      AND is_deactivated = FALSE
    """,
    """
    CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_user_sessions_current_refresh_hash
    ON user_sessions (current_refresh_token_hash)
    """,
    """
    CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_user_sessions_previous_refresh_hash
    ON user_sessions (previous_refresh_token_hash)
    WHERE previous_refresh_token_hash IS NOT NULL
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_sessions_player_active
    ON user_sessions (player_id)
    WHERE revoked_at IS NULL
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_sessions_expires_at
    ON user_sessions (expires_at)
    """,
]

SQLITE_INDEXES = [
    statement.replace(" CONCURRENTLY", "").replace("TRUE", "1").replace("FALSE", "0")
    for statement in POSTGRES_INDEXES
]

SCORE_DUPLICATE_CHECK = """
SELECT session_code, player_id, COUNT(*) AS duplicate_count
FROM scores
GROUP BY session_code, player_id
HAVING COUNT(*) > 1
LIMIT 1
"""

UNIQUE_SCORE_INDEX_POSTGRES = """
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_scores_session_player
ON scores (session_code, player_id)
"""

UNIQUE_SCORE_INDEX_SQLITE = UNIQUE_SCORE_INDEX_POSTGRES.replace(" CONCURRENTLY", "")

PLAYER_RESPONSE_DUPLICATE_CHECK = """
SELECT session_code, player_id, question_id, COUNT(*) AS duplicate_count
FROM player_responses
GROUP BY session_code, player_id, question_id
HAVING COUNT(*) > 1
LIMIT 1
"""

UNIQUE_PLAYER_RESPONSE_INDEX_POSTGRES = """
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_player_responses_session_player_question
ON player_responses (session_code, player_id, question_id)
"""

UNIQUE_PLAYER_RESPONSE_INDEX_SQLITE = UNIQUE_PLAYER_RESPONSE_INDEX_POSTGRES.replace(
    " CONCURRENTLY", ""
)

PLAYER_RESPONSE_DUPLICATE_REMOVAL_COUNT = """
SELECT COALESCE(SUM(duplicate_count - 1), 0) AS duplicate_rows
FROM (
    SELECT COUNT(*) AS duplicate_count
    FROM player_responses
    GROUP BY session_code, player_id, question_id
    HAVING COUNT(*) > 1
) duplicates
"""

DEDUPLICATE_PLAYER_RESPONSES = """
WITH ranked AS (
    SELECT
        response_id,
        ROW_NUMBER() OVER (
            PARTITION BY session_code, player_id, question_id
            ORDER BY (submitted_at IS NULL), submitted_at, response_id
        ) AS row_number
    FROM player_responses
)
DELETE FROM player_responses
WHERE response_id IN (
    SELECT response_id
    FROM ranked
    WHERE row_number > 1
)
"""

VERIFY_PLAYER_RESPONSE_INDEX_POSTGRES = """
SELECT 1
FROM pg_indexes
WHERE schemaname = current_schema()
  AND tablename = 'player_responses'
  AND indexname = 'uq_player_responses_session_player_question'
LIMIT 1
"""

VERIFY_PLAYER_RESPONSE_INDEX_SQLITE = """
SELECT 1
FROM sqlite_master
WHERE type = 'index'
  AND tbl_name = 'player_responses'
  AND name = 'uq_player_responses_session_player_question'
LIMIT 1
"""


def _execute_index(connection, statement: str) -> None:
    connection.execute(text(statement))


def _create_indexes(connection, statements: list[str]) -> None:
    for statement in statements:
        try:
            _execute_index(connection, statement)
        except Exception as exc:
            logger.warning("Could not create performance index: %s", exc)


def _create_unique_score_index(connection, statement: str) -> None:
    try:
        duplicate = connection.execute(text(SCORE_DUPLICATE_CHECK)).first()
        if duplicate:
            logger.warning(
                "Skipping uq_scores_session_player because duplicate score rows exist "
                "for session %s and player %s",
                duplicate.session_code,
                duplicate.player_id,
            )
            return

        _execute_index(connection, statement)
    except Exception as exc:
        logger.warning("Could not create unique score index: %s", exc)


def _deduplicate_player_responses(connection) -> int:
    duplicate_rows = (
        connection.execute(text(PLAYER_RESPONSE_DUPLICATE_REMOVAL_COUNT)).scalar() or 0
    )
    if not duplicate_rows:
        return 0

    logger.warning(
        "Removing %s duplicate player response rows before enforcing unique answers",
        duplicate_rows,
    )
    connection.execute(text(DEDUPLICATE_PLAYER_RESPONSES))
    remaining_duplicate = connection.execute(
        text(PLAYER_RESPONSE_DUPLICATE_CHECK)
    ).first()
    if remaining_duplicate:
        raise RuntimeError(
            "Could not remove duplicate player response rows for "
            f"session={remaining_duplicate.session_code} "
            f"player={remaining_duplicate.player_id} "
            f"question={remaining_duplicate.question_id}"
        )
    return int(duplicate_rows)


def _verify_unique_player_response_index(connection, statement: str) -> None:
    if not connection.execute(text(statement)).first():
        raise RuntimeError(
            "Required unique index uq_player_responses_session_player_question "
            "was not created"
        )


def ensure_required_security_constraints() -> None:
    """Apply required DB invariants that game integrity depends on."""
    is_postgres = engine.dialect.name == "postgresql"
    unique_player_response_statement = (
        UNIQUE_PLAYER_RESPONSE_INDEX_POSTGRES
        if is_postgres
        else UNIQUE_PLAYER_RESPONSE_INDEX_SQLITE
    )
    verify_statement = (
        VERIFY_PLAYER_RESPONSE_INDEX_POSTGRES
        if is_postgres
        else VERIFY_PLAYER_RESPONSE_INDEX_SQLITE
    )

    connectable = engine.connect()
    if is_postgres:
        connectable = connectable.execution_options(isolation_level="AUTOCOMMIT")

    with connectable as connection:
        removed_count = _deduplicate_player_responses(connection)
        _execute_index(connection, unique_player_response_statement)
        _verify_unique_player_response_index(connection, verify_statement)

    logger.info(
        "Required security constraints are ready; removed %s duplicate player response rows",
        removed_count,
    )


def ensure_performance_indexes() -> None:
    """Create indexes used by social, game, score, and refresh-token queries."""
    is_postgres = engine.dialect.name == "postgresql"
    statements = POSTGRES_INDEXES if is_postgres else SQLITE_INDEXES
    unique_score_statement = (
        UNIQUE_SCORE_INDEX_POSTGRES if is_postgres else UNIQUE_SCORE_INDEX_SQLITE
    )

    connectable = engine.connect()
    if is_postgres:
        connectable = connectable.execution_options(isolation_level="AUTOCOMMIT")

    with connectable as connection:
        _create_indexes(connection, statements)
        _create_unique_score_index(connection, unique_score_statement)

    logger.info("Performance indexes are ready")
