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

SCORE_DUPLICATE_REMOVAL_COUNT = """
SELECT COALESCE(SUM(duplicate_count - 1), 0) AS duplicate_rows
FROM (
    SELECT COUNT(*) AS duplicate_count
    FROM scores
    GROUP BY session_code, player_id
    HAVING COUNT(*) > 1
) duplicates
"""

DEDUPLICATE_SCORES = """
WITH ranked AS (
    SELECT
        score_id,
        ROW_NUMBER() OVER (
            PARTITION BY session_code, player_id
            ORDER BY score DESC, (result IS NULL), score_id
        ) AS row_number
    FROM scores
)
DELETE FROM scores
WHERE score_id IN (
    SELECT score_id
    FROM ranked
    WHERE row_number > 1
)
"""

SESSION_ASSIGNMENT_DUPLICATE_CHECK = """
SELECT session_code, player_id, COUNT(*) AS duplicate_count
FROM session_player_assignments
GROUP BY session_code, player_id
HAVING COUNT(*) > 1
LIMIT 1
"""

UNIQUE_SESSION_ASSIGNMENT_INDEX_POSTGRES = """
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_session_player_assignments_session_player
ON session_player_assignments (session_code, player_id)
"""

UNIQUE_SESSION_ASSIGNMENT_INDEX_SQLITE = (
    UNIQUE_SESSION_ASSIGNMENT_INDEX_POSTGRES.replace(" CONCURRENTLY", "")
)

SESSION_ASSIGNMENT_DUPLICATE_REMOVAL_COUNT = """
SELECT COALESCE(SUM(duplicate_count - 1), 0) AS duplicate_rows
FROM (
    SELECT COUNT(*) AS duplicate_count
    FROM session_player_assignments
    GROUP BY session_code, player_id
    HAVING COUNT(*) > 1
) duplicates
"""

DEDUPLICATE_SESSION_ASSIGNMENTS = """
WITH ranked AS (
    SELECT
        assignment_id,
        ROW_NUMBER() OVER (
            PARTITION BY session_code, player_id
            ORDER BY (session_end IS NOT NULL), session_start, assignment_id
        ) AS row_number
    FROM session_player_assignments
)
DELETE FROM session_player_assignments
WHERE assignment_id IN (
    SELECT assignment_id
    FROM ranked
    WHERE row_number > 1
)
"""

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

VERIFY_UNIQUE_INDEX_POSTGRES = """
SELECT
    i.indisunique,
    i.indisvalid,
    i.indisready,
    i.indislive,
    i.indpred IS NULL AS is_not_partial,
    array_agg(a.attname ORDER BY key_ordinal.ordinality) AS column_names
FROM pg_class idx
JOIN pg_index i
    ON i.indexrelid = idx.oid
JOIN pg_class tbl
    ON tbl.oid = i.indrelid
JOIN pg_namespace ns
    ON ns.oid = tbl.relnamespace
JOIN unnest(i.indkey) WITH ORDINALITY AS key_ordinal(attnum, ordinality)
    ON TRUE
JOIN pg_attribute a
    ON a.attrelid = tbl.oid
    AND a.attnum = key_ordinal.attnum
WHERE
    ns.nspname = current_schema()
    AND tbl.relname = :table_name
    AND idx.relname = :index_name
GROUP BY i.indisunique, i.indisvalid, i.indisready, i.indislive, i.indpred
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


def _drop_index(connection, index_name: str, *, is_postgres: bool) -> None:
    concurrent = " CONCURRENTLY" if is_postgres else ""
    connection.execute(text(f'DROP INDEX{concurrent} IF EXISTS "{index_name}"'))


def _verify_unique_index_postgres(
    connection,
    *,
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
) -> None:
    row = connection.execute(
        text(VERIFY_UNIQUE_INDEX_POSTGRES),
        {"table_name": table_name, "index_name": index_name},
    ).first()
    if not row:
        raise RuntimeError(f"Required unique index {index_name} was not created")

    actual_columns = tuple(row.column_names or ())
    if (
        not row.indisunique
        or not row.indisvalid
        or not row.indisready
        or not row.indislive
        or not row.is_not_partial
    ):
        raise RuntimeError(
            f"Required unique index {index_name} is not ready: "
            f"unique={row.indisunique} valid={row.indisvalid} "
            f"ready={row.indisready} live={row.indislive} "
            f"not_partial={row.is_not_partial}"
        )

    if actual_columns != columns:
        raise RuntimeError(
            f"Required unique index {index_name} has columns {actual_columns}, "
            f"expected {columns}"
        )


def _verify_unique_index_sqlite(
    connection,
    *,
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
) -> None:
    indexes = connection.exec_driver_sql(
        f"PRAGMA index_list('{table_name}')"
    ).mappings()
    matching_index = next(
        (index for index in indexes if index["name"] == index_name), None
    )
    if not matching_index:
        raise RuntimeError(f"Required unique index {index_name} was not created")
    if not matching_index["unique"]:
        raise RuntimeError(f"Required index {index_name} is not unique")
    if matching_index.get("partial"):
        raise RuntimeError(f"Required index {index_name} must not be partial")

    indexed_columns = tuple(
        row["name"]
        for row in connection.exec_driver_sql(
            f"PRAGMA index_info('{index_name}')"
        ).mappings()
    )
    if indexed_columns != columns:
        raise RuntimeError(
            f"Required unique index {index_name} has columns {indexed_columns}, "
            f"expected {columns}"
        )


def _verify_unique_index(
    connection,
    *,
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
    is_postgres: bool,
) -> None:
    if is_postgres:
        _verify_unique_index_postgres(
            connection,
            table_name=table_name,
            index_name=index_name,
            columns=columns,
        )
        return

    _verify_unique_index_sqlite(
        connection,
        table_name=table_name,
        index_name=index_name,
        columns=columns,
    )


def _create_and_verify_unique_index(
    connection,
    *,
    create_statement: str,
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
    is_postgres: bool,
) -> None:
    _execute_index(connection, create_statement)
    try:
        _verify_unique_index(
            connection,
            table_name=table_name,
            index_name=index_name,
            columns=columns,
            is_postgres=is_postgres,
        )
    except RuntimeError:
        if not is_postgres:
            raise
        logger.warning(
            "Dropping and recreating invalid or mismatched required index %s",
            index_name,
        )
        _drop_index(connection, index_name, is_postgres=is_postgres)
        _execute_index(connection, create_statement.replace(" IF NOT EXISTS", ""))
        _verify_unique_index(
            connection,
            table_name=table_name,
            index_name=index_name,
            columns=columns,
            is_postgres=is_postgres,
        )


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


def _deduplicate_scores(connection) -> int:
    duplicate_rows = (
        connection.execute(text(SCORE_DUPLICATE_REMOVAL_COUNT)).scalar() or 0
    )
    if not duplicate_rows:
        return 0

    logger.warning(
        "Removing %s duplicate score rows before enforcing unique scores",
        duplicate_rows,
    )
    connection.execute(text(DEDUPLICATE_SCORES))
    remaining_duplicate = connection.execute(text(SCORE_DUPLICATE_CHECK)).first()
    if remaining_duplicate:
        raise RuntimeError(
            "Could not remove duplicate score rows for "
            f"session={remaining_duplicate.session_code} "
            f"player={remaining_duplicate.player_id}"
        )
    return int(duplicate_rows)


def _deduplicate_session_assignments(connection) -> int:
    duplicate_rows = (
        connection.execute(text(SESSION_ASSIGNMENT_DUPLICATE_REMOVAL_COUNT)).scalar()
        or 0
    )
    if not duplicate_rows:
        return 0

    logger.warning(
        "Removing %s duplicate session assignment rows before enforcing membership uniqueness",
        duplicate_rows,
    )
    connection.execute(text(DEDUPLICATE_SESSION_ASSIGNMENTS))
    remaining_duplicate = connection.execute(
        text(SESSION_ASSIGNMENT_DUPLICATE_CHECK)
    ).first()
    if remaining_duplicate:
        raise RuntimeError(
            "Could not remove duplicate session assignment rows for "
            f"session={remaining_duplicate.session_code} "
            f"player={remaining_duplicate.player_id}"
        )
    return int(duplicate_rows)


def ensure_required_security_constraints() -> None:
    """Apply required DB invariants that game integrity depends on."""
    is_postgres = engine.dialect.name == "postgresql"
    unique_player_response_statement = (
        UNIQUE_PLAYER_RESPONSE_INDEX_POSTGRES
        if is_postgres
        else UNIQUE_PLAYER_RESPONSE_INDEX_SQLITE
    )
    unique_score_statement = (
        UNIQUE_SCORE_INDEX_POSTGRES if is_postgres else UNIQUE_SCORE_INDEX_SQLITE
    )
    unique_session_assignment_statement = (
        UNIQUE_SESSION_ASSIGNMENT_INDEX_POSTGRES
        if is_postgres
        else UNIQUE_SESSION_ASSIGNMENT_INDEX_SQLITE
    )

    connectable = engine.connect()
    if is_postgres:
        connectable = connectable.execution_options(isolation_level="AUTOCOMMIT")

    with connectable as connection:
        removed_responses = _deduplicate_player_responses(connection)
        removed_scores = _deduplicate_scores(connection)
        removed_assignments = _deduplicate_session_assignments(connection)

        _create_and_verify_unique_index(
            connection,
            create_statement=unique_player_response_statement,
            table_name="player_responses",
            index_name="uq_player_responses_session_player_question",
            columns=("session_code", "player_id", "question_id"),
            is_postgres=is_postgres,
        )
        _create_and_verify_unique_index(
            connection,
            create_statement=unique_score_statement,
            table_name="scores",
            index_name="uq_scores_session_player",
            columns=("session_code", "player_id"),
            is_postgres=is_postgres,
        )
        _create_and_verify_unique_index(
            connection,
            create_statement=unique_session_assignment_statement,
            table_name="session_player_assignments",
            index_name="uq_session_player_assignments_session_player",
            columns=("session_code", "player_id"),
            is_postgres=is_postgres,
        )

    logger.info(
        "Required security constraints are ready; removed duplicate rows: "
        "player_responses=%s scores=%s session_assignments=%s",
        removed_responses,
        removed_scores,
        removed_assignments,
    )


def ensure_performance_indexes() -> None:
    """Create indexes used by social, game, score, and refresh-token queries."""
    is_postgres = engine.dialect.name == "postgresql"
    statements = POSTGRES_INDEXES if is_postgres else SQLITE_INDEXES
    connectable = engine.connect()
    if is_postgres:
        connectable = connectable.execution_options(isolation_level="AUTOCOMMIT")

    with connectable as connection:
        _create_indexes(connection, statements)

    logger.info("Performance indexes are ready")
