import asyncio
import contextlib
import json
import os
import sys
import types
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy
from sqlalchemy.dialects import postgresql

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

_real_create_engine = sqlalchemy.create_engine


def _create_test_engine(url, *args, **kwargs):
    kwargs.pop("pool_size", None)
    kwargs.pop("max_overflow", None)
    return _real_create_engine("sqlite:///:memory:", *args, **kwargs)


sqlalchemy.create_engine = _create_test_engine

passlib_module = types.ModuleType("passlib")
passlib_context_module = types.ModuleType("passlib.context")


class _CryptContext:
    def __init__(self, *args, **kwargs):
        pass

    def hash(self, password):
        return password

    def verify(self, plain_password, hashed_password):
        return plain_password == hashed_password


passlib_context_module.CryptContext = _CryptContext
passlib_module.context = passlib_context_module
sys.modules.setdefault("passlib", passlib_module)
sys.modules.setdefault("passlib.context", passlib_context_module)

from app.database import dbCRUD
from app.database import performance_migrations
from app.logic import answer_validation, game_logic
from app.routes import game as game_routes
from app.routes import players as player_routes
from app.schemas.game_state_models import GameSessionState
from app.security import game_phase, rate_limit
from app.websockets import (
    game_handlers,
    game_lifecycle,
    game_modes,
    redis_bus,
    routes,
    scheduler,
)
from app.websockets.manager import OutboundQueueItem, SessionPhase, manager
import load_test_websocket

sqlalchemy.create_engine = _real_create_engine


class _FakeRedisPipeline:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.operations = []

    def hset(self, key, field, value):
        self.operations.append(("hset", key, field, value))
        return self

    def hdel(self, key, *fields):
        self.operations.append(("hdel", key, fields))
        return self

    def zadd(self, key, values):
        self.operations.append(("zadd", key, values))
        return self

    def zrem(self, key, *members):
        self.operations.append(("zrem", key, members))
        return self

    def expire(self, key, ttl):
        self.operations.append(("expire", key, ttl))
        return self

    def execute(self):
        for operation in self.operations:
            if operation[0] == "hset":
                _, key, field, value = operation
                self.redis_client.hset(key, field, value)
            elif operation[0] == "hdel":
                _, key, fields = operation
                self.redis_client.hdel(key, *fields)
            elif operation[0] == "zadd":
                _, key, values = operation
                self.redis_client.zadd(key, values)
            elif operation[0] == "zrem":
                _, key, members = operation
                self.redis_client.zrem(key, *members)
            elif operation[0] == "expire":
                _, key, ttl = operation
                self.redis_client.expire(key, ttl)
        self.operations.clear()


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.zsets = {}
        self.eval_calls = []
        self.hget_calls = []
        self.hgetall_calls = []
        self.expire_calls = []
        self.delete_calls = []

    def pipeline(self):
        return _FakeRedisPipeline(self)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hget(self, key, field):
        self.hget_calls.append((key, field))
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key):
        self.hgetall_calls.append(key)
        return dict(self.hashes.get(key, {}))

    def hdel(self, key, *fields):
        hash_value = self.hashes.get(key, {})
        if len(fields) == 1 and isinstance(fields[0], (list, tuple)):
            fields = tuple(fields[0])
        for item in fields:
            hash_value.pop(item, None)

    def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return True

    def hmget(self, key, members):
        return [self.hashes.get(key, {}).get(member) for member in members]

    def zadd(self, key, values):
        self.zsets.setdefault(key, {}).update(values)

    def zrem(self, key, *members):
        zset = self.zsets.get(key, {})
        for member in members:
            zset.pop(member, None)

    def zrangebyscore(self, key, min_score, max_score):
        def score_value(value):
            if value == "-inf":
                return float("-inf")
            if value == "+inf":
                return float("inf")
            return float(value)

        low = score_value(min_score)
        high = score_value(max_score)
        return [
            member
            for member, score in self.zsets.get(key, {}).items()
            if low <= float(score) <= high
        ]

    def zremrangebyscore(self, key, min_score, max_score):
        members = self.zrangebyscore(key, min_score, max_score)
        self.zrem(key, *members)
        return len(members)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, *keys):
        self.delete_calls.append(keys)
        for key in keys:
            self.values.pop(key, None)
            self.hashes.pop(key, None)

    def eval(self, script, numkeys, key, *args):
        self.eval_calls.append((script, numkeys, key, args))
        if "redis.call('ZRANGEBYSCORE'" in script and "redis.call('HDEL'" in script:
            meta_key = args[0]
            now = args[1]
            expired_members = self.zrangebyscore(key, "-inf", now)
            self.zrem(key, *expired_members)
            for member in expired_members:
                self.hdel(meta_key, member)
            return len(expired_members)
        if "redis.call('GET', KEYS[1]) == ARGV[1]" in script:
            if self.values.get(key) == args[0]:
                self.values.pop(key, None)
                return 1
            return 0

        old = self.values.get(key)
        self.values[key] = args[0]
        return old


class _FakeAsyncRedis:
    def __init__(self):
        self.values = {}
        self.get_calls = []
        self.eval_calls = []
        self.expire_calls = []

    async def get(self, key):
        self.get_calls.append(key)
        return self.values.get(key)

    async def eval(self, script, numkeys, key, *args):
        self.eval_calls.append((script, numkeys, key, args))
        if "redis.call('DEL', KEYS[1])" in script:
            if self.values.get(key) == args[0]:
                self.values.pop(key, None)
                return 1
            return 0
        if "redis.call('EXPIRE', KEYS[1], ARGV[2])" in script:
            if self.values.get(key) == args[0]:
                self.expire_calls.append((key, args[1]))
                return 1
            return 0

        old = self.values.get(key)
        self.values[key] = args[0]
        return old


class _FakeAsyncRateLimitRedis:
    def __init__(self, count: int, ttl: int):
        self.count = count
        self.ttl = ttl
        self.eval_calls = []

    async def eval(self, script, numkeys, key, window_seconds):
        self.eval_calls.append((script, numkeys, key, window_seconds))
        return [self.count, self.ttl]


def test_game_session_state_model_restores_timestamp_columns():
    assert hasattr(GameSessionState, "started_at")
    assert hasattr(GameSessionState, "ended_at")


def test_rate_limiter_uses_single_redis_lua_hit():
    limiter = rate_limit.RateLimiter()
    fake_redis = _FakeAsyncRateLimitRedis(count=3, ttl=42)
    limiter._redis = fake_redis

    allowed, retry_after = asyncio.run(limiter._hit_redis("rl:test", 5, 60))

    assert allowed is True
    assert retry_after == 42
    assert len(fake_redis.eval_calls) == 1
    script, numkeys, key, window_seconds = fake_redis.eval_calls[0]
    assert "INCR" in script
    assert "TTL" in script
    assert numkeys == 1
    assert key == "rl:test"
    assert window_seconds == 60


def test_create_game_session_cleans_up_partial_setup_failures():
    mock_db = MagicMock()

    def refresh_side_effect(obj):
        obj.session_code = "SESSION123"
        obj.host_name = "Host"
        obj.number_of_questions = 5
        obj.game_code = "GAME1"
        obj.owner_player_id = "OWNER1"

    mock_db.refresh.side_effect = refresh_side_effect

    with patch.object(dbCRUD, "generate_session_code", return_value="SESSION123"):
        with patch.object(dbCRUD, "add_question_to_session"):
            with patch.object(
                dbCRUD,
                "create_game_session_state",
                side_effect=RuntimeError("state init failed"),
            ):
                with patch.object(dbCRUD, "_cleanup_partial_game_session") as cleanup:
                    with pytest.raises(RuntimeError, match="state init failed"):
                        dbCRUD.create_game_session(
                            mock_db,
                            "Host",
                            5,
                            "GAME1",
                            "OWNER1",
                            True,
                            "easy",
                        )

    cleanup.assert_called_once_with(mock_db, "SESSION123")


def test_get_session_details_uses_ispublic_field():
    started_at = datetime(2026, 4, 3, 12, 0, 0)
    session = SimpleNamespace(
        session_code="SESSION123",
        host_name="Host",
        game_code="GAME1",
        number_of_questions=5,
    )
    game = SimpleNamespace(genre="Science")
    game_state = SimpleNamespace(
        is_active=True,
        ispublic=False,
        started_at=started_at,
        ended_at=None,
    )

    with patch.object(dbCRUD, "get_session_by_code", return_value=session):
        with patch.object(dbCRUD, "get_game_by_code", return_value=game):
            with patch.object(
                dbCRUD, "get_game_session_state", return_value=game_state
            ):
                result = dbCRUD.get_session_details(MagicMock(), "SESSION123")

    assert result["is_public"] is False
    assert result["created_at"] == started_at


def test_update_game_start_status_sets_started_at():
    mock_db = MagicMock()
    game_state = SimpleNamespace(isstarted=False, started_at=None)

    with patch.object(game_logic, "get_game_session_state", return_value=game_state):
        game_logic.updateGameStartStatus(mock_db, "SESSION123", True)

    assert game_state.isstarted is True
    assert game_state.started_at is not None
    mock_db.flush.assert_called_once()
    mock_db.commit.assert_not_called()


def test_question_fallback_without_options_does_not_expose_answer():
    question = SimpleNamespace(
        question_id="Q1",
        question="What is 2 + 2?",
        answer="4",
        genre="Math",
        difficulty=SimpleNamespace(value="easy"),
    )

    with patch.object(game_logic, "get_question_by_id", return_value=question):
        result = game_logic.get_question_with_randomized_options(MagicMock(), "Q1")

    assert result["question_options"] == []
    assert result["display_options"] == []
    assert result["correct_index"] is None


def test_answer_validation_normalizes_punctuation_and_accents():
    result = answer_validation.validate_answer("  Beyonce!  ", ["Beyonce"])

    assert result.is_correct is True
    assert result.method == "exact"


def test_answer_validation_accepts_close_spelling():
    result = answer_validation.validate_answer("James Camaron", ["James Cameron"])

    assert result.is_correct is True
    assert result.method.startswith("levenshtein:")


def test_answer_validation_rejects_wrong_short_numeric_answer():
    result = answer_validation.validate_answer("5", ["7"])

    assert result.is_correct is False


def test_answer_validation_rejects_wrong_short_text_answer():
    result = answer_validation.validate_answer("uk", ["us"])

    assert result.is_correct is False


def test_answer_validation_rejects_wrong_chemical_formula():
    result = answer_validation.validate_answer("CaCl2", ["NaCl"])

    assert result.is_correct is False


def test_answer_validation_accepts_compact_formula_spacing():
    result = answer_validation.validate_answer("Na Cl", ["NaCl"])

    assert result.is_correct is True
    assert result.method == "exact_compact"


def test_answer_validation_exact_mode_rejects_close_multiple_choice_answer():
    result = answer_validation.validate_answer("5", ["7"], allow_fuzzy=False)
    decimal_result = answer_validation.validate_answer("7.0", ["7"], allow_fuzzy=False)

    assert result.is_correct is False
    assert result.method == "no_match"
    assert decimal_result.is_correct is False


def test_answer_validation_accepts_equivalent_numeric_text_answer():
    result = answer_validation.validate_answer("7.0", ["7"])

    assert result.is_correct is True
    assert result.method == "numeric"


def test_answer_validation_accepts_aliases_from_question():
    question = SimpleNamespace(
        answer="James Cameron",
        accepted_answers=["Cameron", "Jim Cameron"],
    )

    result = answer_validation.validate_answer_against_question("cameron", question)

    assert result.is_correct is True
    assert result.matched_answer == "Cameron"


def test_submit_player_answer_returns_answer_match_metadata():
    question = SimpleNamespace(answer="James Cameron", accepted_answers=["Cameron"])
    game_state = SimpleNamespace(
        is_active=True,
        isstarted=True,
        current_question_id="Q1",
        fair_play_enabled=False,
    )
    mock_db = MagicMock()

    with patch.object(game_logic, "get_game_session_state", return_value=game_state):
        with patch.object(game_logic, "get_player_response", return_value=None):
            with patch.object(game_logic, "get_question_by_id", return_value=question):
                with patch.object(game_logic, "create_player_response"):
                    with patch.object(game_logic, "update_scores"):
                        with patch.object(
                            game_logic,
                            "check_progression_readiness_without_lock",
                            return_value={"ready_for_progression": True},
                        ):
                            with patch.object(
                                game_logic,
                                "check_and_advance_game",
                                return_value={"players_answered": 1},
                            ):
                                result = game_logic.submit_player_answer(
                                    mock_db,
                                    "SESSION123",
                                    "P1",
                                    "Q1",
                                    "Camron",
                                )

    assert result["is_correct"] is True
    assert "matched_answer" not in result["answer_match"]


def test_submit_player_answer_uses_exact_validation_for_multiple_choice():
    question = SimpleNamespace(
        answer="7",
        accepted_answers=[],
        difficulty="easy",
        question_options=["5", "6", "8"],
    )
    game_state = SimpleNamespace(
        is_active=True,
        isstarted=True,
        current_question_id="Q1",
        fair_play_enabled=False,
    )
    mock_db = MagicMock()

    with patch.object(game_logic, "get_game_session_state", return_value=game_state):
        with patch.object(game_logic, "get_player_response", return_value=None):
            with patch.object(game_logic, "get_question_by_id", return_value=question):
                with patch.object(game_logic, "create_player_response"):
                    with patch.object(game_logic, "update_scores"):
                        with patch.object(
                            game_logic,
                            "check_progression_readiness_without_lock",
                            return_value={"ready_for_progression": True},
                        ):
                            with patch.object(
                                game_logic,
                                "check_and_advance_game",
                                return_value={"players_answered": 1},
                            ):
                                result = game_logic.submit_player_answer(
                                    mock_db,
                                    "SESSION123",
                                    "P1",
                                    "Q1",
                                    "8",
                                )

    assert result["is_correct"] is False


def test_submit_player_answer_uses_fuzzy_validation_for_hard_text_input():
    question = SimpleNamespace(
        answer="James Cameron",
        accepted_answers=[],
        difficulty="hard",
        question_options=["Wrong", "Options", "Can", "Exist"],
    )
    game_state = SimpleNamespace(
        is_active=True,
        isstarted=True,
        current_question_id="Q1",
        fair_play_enabled=False,
    )
    mock_db = MagicMock()

    with patch.object(game_logic, "get_game_session_state", return_value=game_state):
        with patch.object(game_logic, "get_player_response", return_value=None):
            with patch.object(game_logic, "get_question_by_id", return_value=question):
                with patch.object(game_logic, "create_player_response"):
                    with patch.object(game_logic, "update_scores"):
                        with patch.object(
                            game_logic,
                            "check_progression_readiness_without_lock",
                            return_value={"ready_for_progression": True},
                        ):
                            with patch.object(
                                game_logic,
                                "check_and_advance_game",
                                return_value={"players_answered": 1},
                            ):
                                result = game_logic.submit_player_answer(
                                    mock_db,
                                    "SESSION123",
                                    "P1",
                                    "Q1",
                                    "James Camaron",
                                )

    assert result["is_correct"] is True


def test_submit_player_answer_rejects_non_current_question():
    game_state = SimpleNamespace(
        is_active=True,
        isstarted=True,
        current_question_id="Q2",
        fair_play_enabled=False,
    )
    mock_db = MagicMock()

    with patch.object(game_logic, "get_game_session_state", return_value=game_state):
        result = game_logic.submit_player_answer(
            mock_db,
            "SESSION123",
            "P1",
            "Q1",
            "A",
        )

    assert result == {"error": "Question is no longer active"}


def test_submit_player_answer_skips_locked_progression_when_still_waiting():
    question = SimpleNamespace(
        answer="A",
        accepted_answers=[],
        difficulty="easy",
        question_options=["A", "B", "C"],
    )
    game_state = SimpleNamespace(
        is_active=True,
        isstarted=True,
        current_question_id="Q1",
        current_question_index=0,
        total_questions=5,
        fair_play_enabled=False,
    )
    mock_db = MagicMock()
    waiting_progression = {
        "players_total": 3,
        "players_answered": 1,
        "waiting_for_players": True,
        "ready_for_progression": False,
        "progression_lock_skipped": True,
    }

    with patch.object(game_logic, "get_game_session_state", return_value=game_state):
        with patch.object(game_logic, "get_player_response", return_value=None):
            with patch.object(game_logic, "get_question_by_id", return_value=question):
                with patch.object(game_logic, "create_player_response"):
                    with patch.object(game_logic, "update_scores"):
                        with patch.object(
                            game_logic,
                            "get_session_by_code",
                            return_value=SimpleNamespace(owner_player_id="HOST1"),
                        ):
                            with patch.object(game_logic, "set_rls_current_player"):
                                with patch.object(
                                    game_logic,
                                    "check_progression_readiness_without_lock",
                                    return_value=waiting_progression,
                                ) as precheck:
                                    with patch.object(
                                        game_logic,
                                        "check_and_advance_game",
                                    ) as locked_progression:
                                        result = game_logic.submit_player_answer(
                                            mock_db,
                                            "SESSION123",
                                            "P1",
                                            "Q1",
                                            "A",
                                        )

    precheck.assert_called_once_with(mock_db, "SESSION123", "Q1", game_state)
    locked_progression.assert_not_called()
    mock_db.commit.assert_called_once()
    assert result["game_state"]["progression_lock_skipped"] is True
    assert result["game_state"]["waiting_for_players"] is True


def test_submit_player_answer_commits_before_unlocked_progression_precheck():
    events = []
    question = SimpleNamespace(
        answer="A",
        accepted_answers=[],
        difficulty="easy",
        question_options=["A", "B", "C"],
    )
    game_state = SimpleNamespace(
        is_active=True,
        isstarted=True,
        current_question_id="Q1",
        current_question_index=0,
        total_questions=5,
        fair_play_enabled=False,
    )
    mock_db = MagicMock()

    def commit():
        events.append("commit")

    def precheck(db, session_code, question_id, progression_state):
        events.append("precheck")
        return {
            "players_total": 2,
            "players_answered": 1,
            "waiting_for_players": True,
            "ready_for_progression": False,
            "progression_lock_skipped": True,
        }

    mock_db.commit.side_effect = commit

    with patch.object(game_logic, "get_game_session_state", return_value=game_state):
        with patch.object(game_logic, "get_player_response", return_value=None):
            with patch.object(game_logic, "get_question_by_id", return_value=question):
                with patch.object(game_logic, "create_player_response"):
                    with patch.object(game_logic, "update_scores"):
                        with patch.object(
                            game_logic,
                            "get_session_by_code",
                            return_value=SimpleNamespace(owner_player_id="HOST1"),
                        ):
                            with patch.object(game_logic, "set_rls_current_player"):
                                with patch.object(
                                    game_logic,
                                    "check_progression_readiness_without_lock",
                                    side_effect=precheck,
                                ):
                                    with patch.object(
                                        game_logic,
                                        "check_and_advance_game",
                                    ) as locked_progression:
                                        result = game_logic.submit_player_answer(
                                            mock_db,
                                            "SESSION123",
                                            "P1",
                                            "Q1",
                                            "A",
                                        )

    assert events == ["commit", "precheck"]
    locked_progression.assert_not_called()
    assert result["game_state"]["waiting_for_players"] is True


def test_buzzer_hard_answer_payload_uses_text_input_without_options():
    handler = game_handlers.BuzzerGameHandler("SESSION123")

    result = handler.format_buzzer_answer_payload(
        {
            "question_id": "Q1",
            "question": "What is the chemical formula for table salt?",
            "difficulty": "hard",
            "display_options": ["NaCl", "CaCl2", "KBr", "H2O"],
        }
    )

    assert result["ui_mode"] == "text_input"
    assert result["display_options"] == []
    assert result["options"] == []


def test_check_and_advance_game_counts_fair_play_resolved_players():
    game_state = SimpleNamespace(
        isstarted=True,
        current_question_index=0,
        total_questions=2,
        is_active=True,
    )

    with patch.object(game_logic, "get_number_of_players_in_session", return_value=2):
        with patch.object(game_logic, "count_kicked_players", return_value=0):
            with patch.object(
                game_logic, "count_responses_for_question", return_value=1
            ):
                with patch.object(
                    game_logic,
                    "count_fair_play_resolved_players_for_question",
                    return_value=1,
                ):
                    with patch.object(
                        game_logic,
                        "lock_game_session_state_for_update",
                        return_value=game_state,
                    ):
                        with patch.object(
                            game_logic,
                            "get_game_session_state",
                            return_value=game_state,
                        ):
                            with patch.object(
                                game_logic, "update_game_state_waiting_status"
                            ):
                                with patch.object(
                                    game_logic,
                                    "advance_to_next_question",
                                    return_value={"action": "next_question"},
                                ):
                                    result = game_logic.check_and_advance_game(
                                        MagicMock(), "SESSION123", "Q1"
                                    )

    assert result["players_answered"] == 2
    assert result["submitted_answers"] == 1
    assert result["fair_play_resolved"] == 1
    assert result["waiting_for_players"] is False
    assert result["action"] == "next_question"


def test_check_and_advance_game_ignores_kicked_players_denominator():
    game_state = SimpleNamespace(
        isstarted=True,
        current_question_index=0,
        total_questions=1,
        is_active=True,
    )

    with patch.object(game_logic, "get_number_of_players_in_session", return_value=3):
        with patch.object(game_logic, "count_kicked_players", return_value=1):
            with patch.object(
                game_logic, "count_responses_for_question", return_value=2
            ):
                with patch.object(
                    game_logic,
                    "count_fair_play_resolved_players_for_question",
                    return_value=0,
                ):
                    with patch.object(
                        game_logic,
                        "lock_game_session_state_for_update",
                        return_value=game_state,
                    ):
                        with patch.object(
                            game_logic, "update_game_state_waiting_status"
                        ):
                            with patch.object(
                                game_logic,
                                "advance_to_next_question",
                                return_value={"action": "game_ended"},
                            ):
                                result = game_logic.check_and_advance_game(
                                    MagicMock(), "SESSION123", "Q1"
                                )

    assert result["total_joined_players"] == 3
    assert result["kicked_players"] == 1
    assert result["eligible_players"] == 2
    assert result["players_answered"] == 2
    assert result["action"] == "game_ended"


def test_check_and_advance_game_locks_state_before_counting_responses():
    events = []
    game_state = SimpleNamespace(
        isstarted=True,
        current_question_index=0,
        current_question_id="Q1",
        total_questions=2,
        is_active=True,
    )

    def lock_state(db, session_code):
        events.append("lock")
        return game_state

    def count_players(db, session_code):
        events.append("count_players")
        return 1

    def count_responses(db, session_code, question_id):
        events.append("count_responses")
        return 0

    with patch.object(
        game_logic, "lock_game_session_state_for_update", side_effect=lock_state
    ):
        with patch.object(
            game_logic, "get_number_of_players_in_session", side_effect=count_players
        ):
            with patch.object(game_logic, "count_kicked_players", return_value=0):
                with patch.object(
                    game_logic,
                    "count_responses_for_question",
                    side_effect=count_responses,
                ):
                    with patch.object(
                        game_logic,
                        "count_fair_play_resolved_players_for_question",
                        return_value=0,
                    ):
                        result = game_logic.check_and_advance_game(
                            MagicMock(), "SESSION123", "Q1"
                        )

    assert events == ["lock", "count_players", "count_responses"]
    assert result["waiting_for_players"] is True


def test_check_and_advance_game_skips_stale_question_after_lock():
    game_state = SimpleNamespace(
        isstarted=True,
        current_question_index=1,
        current_question_id="Q2",
        total_questions=5,
        is_active=True,
    )

    with patch.object(
        game_logic,
        "lock_game_session_state_for_update",
        return_value=game_state,
    ):
        with patch.object(
            game_logic, "get_number_of_players_in_session"
        ) as count_players:
            result = game_logic.check_and_advance_game(MagicMock(), "SESSION123", "Q1")

    count_players.assert_not_called()
    assert result["stale_question"] is True
    assert result["current_question_index"] == 1


def test_join_game_checks_active_session_without_locking_host_row():
    mock_db = MagicMock()
    game_session = SimpleNamespace(session_code="SESSION123")
    player = SimpleNamespace(player_id="P1", active_game_code=None)

    game_query = MagicMock()
    game_query.join.return_value = game_query
    game_query.filter.return_value = game_query
    game_query.with_for_update.return_value = game_query
    game_query.first.return_value = game_session

    player_query = MagicMock()
    player_query.filter.return_value = player_query
    player_query.with_for_update.return_value = player_query
    player_query.first.return_value = player

    mock_db.query.side_effect = [game_query, player_query]

    with patch.object(dbCRUD, "assign_player_to_session") as assign_player:
        with patch.object(dbCRUD, "create_score") as create_score:
            result = dbCRUD.join_game(mock_db, "SESSION123", "P1")

    assert result is game_session
    game_query.with_for_update.assert_not_called()
    player_query.with_for_update.assert_called_once_with()
    assign_player.assert_called_once_with(mock_db, "P1", "SESSION123")
    create_score.assert_called_once_with(mock_db, "SESSION123", "P1")
    mock_db.commit.assert_called_once()


def test_join_game_rejects_inactive_sessions_before_membership_changes():
    mock_db = MagicMock()
    game_query = MagicMock()
    game_query.join.return_value = game_query
    game_query.filter.return_value = game_query
    game_query.with_for_update.return_value = game_query
    game_query.first.return_value = None
    mock_db.query.return_value = game_query

    with patch.object(dbCRUD, "assign_player_to_session") as assign_player:
        with patch.object(dbCRUD, "create_score") as create_score:
            with pytest.raises(ValueError, match="Game session not found"):
                dbCRUD.join_game(mock_db, "SESSION123", "P1")

    assign_player.assert_not_called()
    create_score.assert_not_called()
    mock_db.commit.assert_not_called()


def test_assign_player_to_session_does_not_rewrite_active_session_start():
    mock_db = MagicMock()
    original_start = datetime(2026, 6, 1, 12, 0, 0)
    assignment = SimpleNamespace(session_start=original_start, session_end=None)

    with patch.object(
        dbCRUD,
        "ensure_session_assignment_with_status",
        return_value=(assignment, False),
    ):
        dbCRUD.assign_player_to_session(mock_db, "P1", "SESSION123")

    assert assignment.session_start == original_start
    assert assignment.session_end is None
    mock_db.flush.assert_called_once()


def test_assign_player_to_session_reopens_ended_membership_with_new_start():
    mock_db = MagicMock()
    original_start = datetime(2026, 6, 1, 12, 0, 0)
    ended_at = datetime(2026, 6, 1, 12, 30, 0)
    new_start = datetime(2026, 6, 1, 13, 0, 0)
    assignment = SimpleNamespace(session_start=original_start, session_end=ended_at)

    with patch.object(
        dbCRUD,
        "ensure_session_assignment_with_status",
        return_value=(assignment, False),
    ):
        with patch.object(dbCRUD, "utc_now", return_value=new_start):
            dbCRUD.assign_player_to_session(mock_db, "P1", "SESSION123")

    assert assignment.session_start == new_start
    assert assignment.session_end is None
    mock_db.flush.assert_called_once()


def test_join_queue_defaults_to_direct_idempotent_join():
    request = SimpleNamespace(session_code="SESSION123", websocket_id=None)
    http_request = MagicMock()
    current_player = SimpleNamespace(player_id="P1")
    db = MagicMock()

    with patch.object(game_routes, "USE_PROCESS_LOCAL_JOIN_QUEUE", False):
        with patch.object(game_routes, "enforce_rate_limit", AsyncMock()):
            with patch.object(game_routes, "get_client_ip", return_value="127.0.0.1"):
                with patch.object(game_routes, "assert_public_or_member_or_owner"):
                    with patch.object(
                        game_routes, "is_session_member", return_value=False
                    ):
                        with patch.object(
                            game_routes.asyncio,
                            "to_thread",
                            AsyncMock(),
                        ) as to_thread:
                            with patch.object(
                                game_routes.join_queue_manager,
                                "add_to_queue",
                                AsyncMock(),
                            ) as add_to_queue:
                                response = asyncio.run(
                                    game_routes.join_game_queue(
                                        request,
                                        http_request,
                                        current_player,
                                        db,
                                    )
                                )

    to_thread.assert_awaited_once_with(
        game_routes._join_game_in_thread_session,
        "SESSION123",
        "P1",
    )
    add_to_queue.assert_not_called()
    assert response.success is True
    assert response.queue_id is None
    assert response.estimated_wait_time == 0


def test_join_game_thread_helper_uses_thread_local_session_and_rls():
    thread_db = MagicMock()
    session_factory = MagicMock(return_value=thread_db)

    with patch.object(game_routes, "SessionLocal", session_factory):
        with patch.object(game_routes, "set_rls_current_player") as set_rls:
            with patch.object(game_routes, "clear_rls_context") as clear_rls:
                with patch.object(
                    game_routes,
                    "join_game",
                    return_value="joined",
                ) as join_game:
                    result = game_routes._join_game_in_thread_session(
                        "SESSION123",
                        "P1",
                    )

    assert result == "joined"
    set_rls.assert_called_once_with(thread_db, "P1")
    join_game.assert_called_once_with(thread_db, "SESSION123", "P1")
    clear_rls.assert_called_once_with(thread_db)
    thread_db.close.assert_called_once()


def test_score_and_session_assignment_models_prevent_duplicate_membership_rows():
    score_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in dbCRUD.Scores.__table__.constraints
        if isinstance(constraint, sqlalchemy.UniqueConstraint)
    }
    assignment_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in dbCRUD.SessionAssignment.__table__.constraints
        if isinstance(constraint, sqlalchemy.UniqueConstraint)
    }

    assert score_constraints["uq_scores_session_player"] == (
        "session_code",
        "player_id",
    )
    assert assignment_constraints["uq_session_player_assignments_session_player"] == (
        "session_code",
        "player_id",
    )


def test_postgres_session_assignment_uses_on_conflict_do_nothing():
    mock_db = MagicMock()
    assignment = SimpleNamespace(
        assignment_id="A1",
        session_code="SESSION123",
        player_id="P1",
    )
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = assignment
    mock_db.query.return_value = query
    mock_db.execute.return_value.scalar_one_or_none.return_value = "A1"

    with patch.object(dbCRUD, "_is_postgresql_session", return_value=True):
        result = dbCRUD.ensure_session_assignment(mock_db, "SESSION123", "P1")

    statement = mock_db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert result is assignment
    assert "ON CONFLICT" in compiled
    assert "DO NOTHING" in compiled
    assert "session_code" in compiled
    assert "player_id" in compiled


def test_postgres_score_creation_uses_on_conflict_do_nothing():
    mock_db = MagicMock()
    score = SimpleNamespace(score_id="S1", score=14)
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = score
    mock_db.query.return_value = query
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with patch.object(dbCRUD, "_is_postgresql_session", return_value=True):
        with patch.object(dbCRUD, "ensure_session_assignment") as ensure_assignment:
            with patch.object(
                dbCRUD,
                "get_player_by_ID",
                return_value=SimpleNamespace(
                    player_name="Alice",
                    profile_photo_url="photo.jpg",
                ),
            ):
                result = dbCRUD.create_score(mock_db, "SESSION123", "P1")

    statement = mock_db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert result is score
    ensure_assignment.assert_called_once_with(mock_db, "SESSION123", "P1")
    assert "ON CONFLICT" in compiled
    assert "DO NOTHING" in compiled
    assert "score = " not in compiled


def test_security_constraint_migration_deduplicates_scores_and_assignments():
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE scores (
                score_id TEXT PRIMARY KEY,
                session_code TEXT,
                player_id TEXT,
                score INTEGER,
                result TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE session_player_assignments (
                assignment_id TEXT PRIMARY KEY,
                session_code TEXT,
                player_id TEXT,
                session_start TEXT,
                session_end TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO scores
                (score_id, session_code, player_id, score, result)
            VALUES
                ('S_LOW', 'SESSION123', 'P1', 1, NULL),
                ('S_HIGH', 'SESSION123', 'P1', 5, 'win')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO session_player_assignments
                (assignment_id, session_code, player_id, session_start, session_end)
            VALUES
                ('A_ACTIVE', 'SESSION123', 'P1', '2026-01-01', NULL),
                ('A_OLD', 'SESSION123', 'P1', '2025-01-01', '2025-01-02')
            """
        )

        removed_scores = performance_migrations._deduplicate_scores(connection)
        removed_assignments = performance_migrations._deduplicate_session_assignments(
            connection
        )

        remaining_score = connection.exec_driver_sql(
            "SELECT score_id FROM scores"
        ).scalar()
        remaining_assignment = connection.exec_driver_sql(
            "SELECT assignment_id FROM session_player_assignments"
        ).scalar()

    assert removed_scores == 1
    assert removed_assignments == 1
    assert remaining_score == "S_HIGH"
    assert remaining_assignment == "A_ACTIVE"


def test_sqlite_unique_index_verification_rejects_partial_same_named_index():
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE scores (
                score_id TEXT PRIMARY KEY,
                session_code TEXT,
                player_id TEXT,
                score INTEGER
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE UNIQUE INDEX uq_scores_session_player
            ON scores (session_code, player_id)
            WHERE score > 0
            """
        )

        with pytest.raises(RuntimeError, match="must not be partial"):
            performance_migrations._verify_unique_index_sqlite(
                connection,
                table_name="scores",
                index_name="uq_scores_session_player",
                columns=("session_code", "player_id"),
            )


def test_postgres_invalid_unique_index_is_dropped_and_recreated():
    class FakeResult:
        def __init__(self, row=None):
            self.row = row

        def first(self):
            return self.row

    invalid_row = SimpleNamespace(
        column_names=("session_code", "player_id"),
        indisunique=True,
        indisvalid=False,
        indisready=True,
        indislive=True,
        is_not_partial=True,
    )
    valid_row = SimpleNamespace(
        column_names=("session_code", "player_id"),
        indisunique=True,
        indisvalid=True,
        indisready=True,
        indislive=True,
        is_not_partial=True,
    )

    class FakeConnection:
        def __init__(self):
            self.rows = [invalid_row, valid_row]
            self.statements = []

        def execute(self, statement, params=None):
            sql = str(statement)
            self.statements.append(sql)
            if "FROM pg_class idx" in sql:
                return FakeResult(self.rows.pop(0))
            return FakeResult()

    connection = FakeConnection()

    performance_migrations._create_and_verify_unique_index(
        connection,
        create_statement=performance_migrations.UNIQUE_SCORE_INDEX_POSTGRES,
        table_name="scores",
        index_name="uq_scores_session_player",
        columns=("session_code", "player_id"),
        is_postgres=True,
    )

    assert any("DROP INDEX CONCURRENTLY" in sql for sql in connection.statements)
    assert any(
        "CREATE UNIQUE INDEX CONCURRENTLY uq_scores_session_player" in sql
        for sql in connection.statements
    )


def test_duplicate_phone_registration_returns_generic_response():
    player = SimpleNamespace(
        player_name="Player",
        player_email="player@example.com",
        player_mobile="07708030680",
        hashed_password="secret-password",
    )
    request = MagicMock()
    request.headers = {}

    with patch.object(player_routes, "enforce_rate_limit", new_callable=AsyncMock):
        with patch.object(
            player_routes,
            "enforce_email_verification_send_limits",
            new_callable=AsyncMock,
        ):
            with patch.object(player_routes, "ensure_email_verification_columns"):
                with patch.object(player_routes, "set_rls_login_email"):
                    with patch.object(
                        player_routes, "get_player_by_email", return_value=None
                    ):
                        with patch.object(
                            player_routes,
                            "create_player",
                            side_effect=ValueError(
                                "Account with this phone number already exists"
                            ),
                        ):
                            result = asyncio.run(
                                player_routes.create_player_route(
                                    request,
                                    MagicMock(),
                                    player,
                                    MagicMock(),
                                )
                            )

    assert result == {"message": player_routes.GENERIC_REGISTRATION_MESSAGE}


def test_build_sync_state_recovers_active_game_to_question_not_intro():
    game_state = SimpleNamespace(
        session_code="SESSION123",
        current_question_index=1,
        current_question_id="Q2",
        is_active=True,
        is_waiting_for_players=True,
        isstarted=True,
        total_questions=5,
        ispublic=True,
        started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=60),
        ended_at=None,
    )

    with patch.object(routes, "get_game_session_state", return_value=game_state):
        with patch.object(routes, "manager") as mock_manager:
            mock_manager.get_session_sync_state.return_value = {
                "session_code": "SESSION123",
                "phase": "lobby",
            }
            mock_manager.set_session_phase.return_value = {
                "session_code": "SESSION123",
                "phase": "question",
                "current_question_id": "Q2",
            }
            mock_manager.get_mobile_players.return_value = []
            mock_manager.get_current_question.return_value = None

            result = routes.build_sync_state("SESSION123", MagicMock())

    assert result["phase"] == "question"
    mock_manager.set_session_phase.assert_called_once()
    assert mock_manager.set_session_phase.call_args.args[1] == SessionPhase.QUESTION


def test_reveal_current_question_allows_new_question_while_phase_is_question():
    question = {
        "question_id": "Q2",
        "question": "Next?",
        "genre": "Trivia",
        "difficulty": "easy",
    }

    with patch.object(
        scheduler,
        "get_current_question_details",
        return_value={"current_question": question},
    ):
        with patch.object(scheduler, "manager") as mock_manager:
            mock_manager.get_session_phase_state.return_value = {
                "phase": "question",
                "current_question_id": "Q1",
            }
            mock_manager.set_session_phase.return_value = {
                "phase": "question",
                "server_time_ms": 123,
            }
            mock_manager.broadcast_to_session = AsyncMock()

            result = asyncio.run(
                scheduler.reveal_current_question(
                    "SESSION123", MagicMock(), "2026-06-01T12:00:00Z"
                )
            )

    assert result is True
    mock_manager.queue_question.assert_called_once()
    broadcast_types = [
        call.args[1]["type"]
        for call in mock_manager.broadcast_to_session.await_args_list
    ]
    assert broadcast_types == ["fair_play_question_reset", "question_started"]
    reset_call = mock_manager.broadcast_to_session.await_args_list[0]
    assert reset_call.kwargs["only_client_types"] == ["mobile"]
    assert reset_call.args[1]["data"]["question_id"] == "Q2"
    assert reset_call.args[1]["data"]["is_frozen"] is False


def test_reveal_easy_question_has_no_question_timer():
    question = {
        "question_id": "Q_EASY",
        "question": "Easy one?",
        "genre": "Trivia",
        "difficulty": "easy",
    }

    with patch.object(
        scheduler,
        "get_current_question_details",
        return_value={"current_question": question},
    ):
        with patch.object(scheduler, "manager") as mock_manager:
            mock_manager.get_session_phase_state.return_value = {
                "phase": "question",
                "current_question_id": "Q_PREVIOUS",
                "question_expires_at": "2026-06-01T12:00:30Z",
                "question_duration_ms": 30000,
            }
            mock_manager.set_session_phase.return_value = {
                "phase": "question",
                "server_time_ms": 123,
            }
            mock_manager.broadcast_to_session = AsyncMock()
            with patch.object(scheduler.asyncio, "create_task") as create_task:
                result = asyncio.run(
                    scheduler.reveal_current_question(
                        "SESSION123", MagicMock(), "2026-06-01T12:00:00Z"
                    )
                )

    assert result is True
    queued_question = mock_manager.queue_question.call_args.args[1]
    assert "expires_at" not in queued_question
    assert "duration_ms" not in queued_question
    phase_kwargs = mock_manager.set_session_phase.call_args.kwargs
    assert phase_kwargs["question_expires_at"] is None
    assert phase_kwargs["question_duration_ms"] is None
    assert set(phase_kwargs["clear_fields"]) == {
        "question_expires_at",
        "question_duration_ms",
    }
    create_task.assert_not_called()


def test_reveal_medium_question_keeps_question_timer():
    question = {
        "question_id": "Q_MEDIUM",
        "question": "Medium one?",
        "genre": "Trivia",
        "difficulty": "medium",
    }

    with patch.object(
        scheduler,
        "get_current_question_details",
        return_value={"current_question": question},
    ):
        with patch.object(scheduler, "manager") as mock_manager:
            mock_manager.get_session_phase_state.return_value = {
                "phase": "question",
                "current_question_id": "Q_PREVIOUS",
            }
            mock_manager.set_session_phase.return_value = {
                "phase": "question",
                "server_time_ms": 123,
            }
            mock_manager.broadcast_to_session = AsyncMock()
            timeout = MagicMock(return_value="timeout-task")
            with patch.object(scheduler, "scheduled_question_timeout", new=timeout):
                with patch.object(scheduler.asyncio, "create_task") as create_task:
                    result = asyncio.run(
                        scheduler.reveal_current_question(
                            "SESSION123", MagicMock(), "2026-06-01T12:00:00Z"
                        )
                    )

    assert result is True
    queued_question = mock_manager.queue_question.call_args.args[1]
    assert queued_question["duration_ms"] == scheduler.QUESTION_DURATION_MS
    assert queued_question["expires_at"] == "2026-06-01T12:00:30Z"
    phase_kwargs = mock_manager.set_session_phase.call_args.kwargs
    assert phase_kwargs["question_expires_at"] == "2026-06-01T12:00:30Z"
    assert phase_kwargs["question_duration_ms"] == scheduler.QUESTION_DURATION_MS
    assert phase_kwargs["clear_fields"] is None
    create_task.assert_called_once_with("timeout-task")


def test_reveal_current_question_skips_same_question_duplicate():
    question = {"question_id": "Q1", "question": "Same?"}

    with patch.object(
        scheduler,
        "get_current_question_details",
        return_value={"current_question": question},
    ):
        with patch.object(scheduler, "manager") as mock_manager:
            mock_manager.get_session_phase_state.return_value = {
                "phase": "question",
                "current_question_id": "Q1",
            }
            mock_manager.broadcast_to_session = AsyncMock()

            result = asyncio.run(
                scheduler.reveal_current_question(
                    "SESSION123", MagicMock(), "2026-06-01T12:00:00Z"
                )
            )

    assert result is False
    mock_manager.set_session_phase.assert_not_called()
    mock_manager.broadcast_to_session.assert_not_awaited()


def test_countdown_duration_is_server_owned():
    assert scheduler.normalize_countdown_duration_ms(24000, "SESSION123") == 3000
    assert scheduler.normalize_countdown_duration_ms("24000", "SESSION123") == 3000
    assert scheduler.normalize_countdown_duration_ms(None, "SESSION123") == 3000


def test_game_lifecycle_skips_duplicate_broadcast_for_already_ended_session():
    ended_at = datetime(2026, 6, 1, 12, 0, 0)
    game_state = SimpleNamespace(ended_at=ended_at)
    final_scores = [{"player_id": "P1", "player_name": "Player", "score": 2}]
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = game_state

    with patch.object(game_lifecycle, "update_game_session_ended", return_value=False):
        with patch.object(
            game_lifecycle, "get_final_scores", return_value=final_scores
        ):
            with patch.object(game_lifecycle, "manager") as mock_manager:
                mock_manager.set_session_phase.return_value = {
                    "phase": "ended",
                    "phase_started_at": "2026-06-01T12:00:00",
                    "server_time_ms": 123,
                }
                mock_manager.broadcast_to_session = AsyncMock()

                result = asyncio.run(
                    game_lifecycle.handle_game_end("SESSION123", mock_db)
                )

    assert result is False
    mock_manager.clear_question_queue.assert_not_called()
    mock_manager.broadcast_to_session.assert_not_awaited()


def test_game_lifecycle_terminal_snapshot_uses_shared_fair_play_statuses():
    ended_at = datetime(2026, 6, 1, 12, 0, 0)
    game_state = SimpleNamespace(ended_at=ended_at)
    query_result = MagicMock()
    query_result.filter.return_value = query_result
    query_result.first.return_value = game_state
    query_result.all.return_value = []
    mock_db = MagicMock()
    mock_db.query.return_value = query_result
    fair_play_statuses = {
        "P1": {"strike_count": 1, "is_kicked": False},
        "P2": {"strike_count": 3, "is_kicked": True},
    }
    captured_snapshot = {}

    def remember_terminal_session(session_code, snapshot, ttl_seconds):
        captured_snapshot.update(snapshot)
        return snapshot

    with patch.object(game_lifecycle, "update_game_session_ended", return_value=True):
        with patch.object(game_lifecycle, "get_final_scores", return_value=[]):
            with patch.object(
                game_lifecycle,
                "get_session_by_code",
                return_value=SimpleNamespace(owner_player_id="OWNER"),
            ):
                with patch.object(game_lifecycle, "set_rls_current_player"):
                    with patch.object(
                        game_lifecycle.manager,
                        "get_fair_play_statuses",
                        return_value=fair_play_statuses,
                    ) as get_statuses:
                        with patch.object(
                            game_lifecycle.manager,
                            "remember_terminal_session",
                            side_effect=remember_terminal_session,
                        ):
                            with patch.object(
                                game_lifecycle.manager,
                                "set_session_phase",
                                return_value={
                                    "phase": "ended",
                                    "phase_started_at": "2026-06-01T12:00:00",
                                    "server_time_ms": 123,
                                },
                            ):
                                with patch.object(
                                    game_lifecycle.manager,
                                    "broadcast_to_session",
                                    AsyncMock(),
                                ):
                                    with patch.object(
                                        game_lifecycle.manager,
                                        "cleanup_session_later",
                                        return_value=None,
                                    ):
                                        with patch.object(
                                            game_lifecycle.manager,
                                            "cleanup_terminal_session_later",
                                            return_value=None,
                                        ):
                                            result = asyncio.run(
                                                game_lifecycle.handle_game_end(
                                                    "SESSION123", mock_db
                                                )
                                            )

    assert result is True
    get_statuses.assert_called_once_with("SESSION123")
    assert captured_snapshot["fair_play_player_status"]["P2"]["is_kicked"] is True
    assert captured_snapshot["removed_players"] == [
        {
            "player_id": "P2",
            "strike_count": 3,
            "is_kicked": True,
        }
    ]


def test_cleanup_session_expires_shared_state_instead_of_deleting_it():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"
    phase_key = manager._shared_state_key(session_code, "phase")
    fair_play_key = manager._fair_play_status_key(session_code)
    fake_redis.values[phase_key] = json.dumps({"phase": "ended"})
    fake_redis.hashes[fair_play_key] = {
        "P1": json.dumps({"strike_count": 1}),
    }

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        manager.cleanup_session(session_code)

    assert fake_redis.values[phase_key] == json.dumps({"phase": "ended"})
    assert fake_redis.hashes[fair_play_key]["P1"] == json.dumps({"strike_count": 1})
    assert fake_redis.delete_calls == []
    assert (phase_key, manager.TERMINAL_SESSION_TTL_SECONDS) in fake_redis.expire_calls
    assert (
        fair_play_key,
        manager.TERMINAL_SESSION_TTL_SECONDS,
    ) in fake_redis.expire_calls


def test_buzzer_state_is_shared_per_session():
    manager.reset_buzzer_state("SESSION123")

    first_state = manager.get_buzzer_state("SESSION123")
    second_state = manager.get_buzzer_state("SESSION123")

    first_state["current_buzzer_winner"] = "P1"

    assert second_state["current_buzzer_winner"] == "P1"


def test_start_buzzer_question_resets_session_buzzer_state():
    manager.get_buzzer_state("SESSION123")["frozen_players"].add("P1")

    state = manager.start_buzzer_question("SESSION123", "Q2")

    assert state["question_active"] is True
    assert state["transitioning"] is False
    assert state["accepting_buzzes"] is True
    assert state["current_question_id"] == "Q2"
    assert state["current_buzzer_winner"] is None
    assert state["frozen_players"] == set()


def test_lock_buzzer_until_next_question_keeps_old_question_closed():
    session_code = "SESSION_LOCK"
    state = manager.start_buzzer_question(session_code, "Q1")
    state["current_buzzer_winner"] = "P1"

    locked_state = manager.lock_buzzer_until_next_question(session_code)
    payload = manager.format_buzzer_state_update(session_code)

    try:
        assert locked_state["question_active"] is False
        assert locked_state["transitioning"] is True
        assert locked_state["accepting_buzzes"] is False
        assert locked_state["current_question_id"] == "Q1"
        assert locked_state["current_buzzer_winner"] is None
        assert payload["question_id"] == "Q1"
        assert payload["button_state"] == "waiting"
        assert payload["accepting_buzzes"] is False
    finally:
        manager.reset_buzzer_state(session_code)


def test_buzzer_state_update_broadcast_uses_authoritative_state():
    session_code = "BUZZERSTATE"
    state = manager.start_buzzer_question(session_code, "Q1")
    state["current_buzzer_winner"] = "P1"
    state["frozen_players"].add("P2")

    with patch.object(
        manager, "broadcast_to_session", new_callable=AsyncMock
    ) as broadcast:
        try:
            asyncio.run(manager.broadcast_buzzer_state_update(session_code))
        finally:
            manager.reset_buzzer_state(session_code)

    broadcast.assert_awaited_once()
    message = broadcast.await_args.args[1]
    assert message["type"] == "buzzer_state_update"
    assert message["data"]["question_id"] == "Q1"
    assert message["data"]["current_buzzer_winner"] == "P1"
    assert message["data"]["frozen_players"] == ["P2"]
    assert message["data"]["question_active"] is True
    assert message["data"]["transitioning"] is False
    assert message["data"]["accepting_buzzes"] is True
    assert message["data"]["button_state"] == "waiting"
    assert broadcast.await_args.kwargs["only_client_types"] == ["mobile"]
    assert broadcast.await_args.kwargs["require_ack"] is True


def test_advance_or_end_current_question_reveals_next_question():
    with patch.object(
        scheduler,
        "advance_to_next_question",
        return_value={"action": "next_question"},
    ):
        with patch.object(
            scheduler,
            "get_current_question_details",
            return_value={"current_question": {"question_id": "Q2"}},
        ):
            with patch.object(
                scheduler, "reveal_current_question", new_callable=AsyncMock
            ) as reveal:
                reveal.return_value = True

                result = asyncio.run(
                    scheduler.advance_or_end_current_question(
                        "SESSION123", MagicMock(), reason="test"
                    )
                )

    assert result is True
    reveal.assert_awaited_once()


def test_resolve_session_game_type_uses_game_rules():
    session = SimpleNamespace(game_code="GAME1")
    game = SimpleNamespace(rules='{"game_type": "buzzer"}', genre="Trivia")

    with patch.object(game_modes, "get_game_by_code", return_value=game):
        result = game_modes.resolve_session_game_type(
            MagicMock(), "SESSION123", session=session
        )

    assert result == "buzzer"


def test_question_phase_rejects_countdown_answers():
    with patch.object(game_phase, "manager") as mock_manager:
        mock_manager.get_session_phase_state.return_value = {
            "phase": SessionPhase.COUNTDOWN.value,
            "current_question_id": "Q1",
        }

        allowed, reason = game_phase.is_question_accepting_answers("SESSION123", "Q1")

    assert allowed is False
    assert reason == "question_not_active"


def test_question_phase_rejects_future_start_time():
    future_start = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()

    with patch.object(game_phase, "manager") as mock_manager:
        mock_manager.get_session_phase_state.return_value = {
            "phase": SessionPhase.QUESTION.value,
            "current_question_id": "Q1",
            "start_at": future_start,
        }

        allowed, reason = game_phase.is_question_accepting_answers("SESSION123", "Q1")

    assert allowed is False
    assert reason == "question_not_started"


def test_deprecated_ws_question_request_never_returns_question_payload():
    websocket = MagicMock()

    with patch.object(routes, "manager") as mock_manager:
        mock_manager.send_personal_message = AsyncMock(return_value=True)

        asyncio.run(
            routes.handle_get_question_with_options(websocket, "Q1", MagicMock())
        )

    mock_manager.send_personal_message.assert_awaited_once()
    payload = mock_manager.send_personal_message.await_args.args[0]
    assert payload["type"] == "error"
    assert "question_with_options" not in str(payload)


def test_mobile_handshake_ignores_requested_game_type():
    session = SimpleNamespace(game_code="GAME1")
    game = SimpleNamespace(rules="Trivia", genre="Trivia")

    with patch.object(game_modes.manager, "set_session_game_type") as set_game_type:
        with patch.object(game_modes, "get_game_by_code", return_value=game):
            result = game_modes.resolve_session_game_type(
                MagicMock(),
                "SESSION123",
                session=session,
                requested_game_type=None,
            )

    assert result == "trivia"
    set_game_type.assert_called_with("SESSION123", "trivia")


def test_mobile_initial_state_sends_queued_question_during_question_phase():
    websocket = MagicMock()
    queued_question = {"question_id": "Q1", "game_type": "trivia"}

    with patch.object(routes, "get_game_session_state", return_value=None):
        with patch.object(routes, "resolve_session_game_type", return_value="trivia"):
            with patch.object(
                routes,
                "build_sync_state",
                return_value={"phase": "question"},
            ):
                with patch.object(routes, "manager") as mock_manager:
                    mock_manager.get_session_stats.return_value = {}
                    mock_manager.get_mobile_players.return_value = []
                    mock_manager.get_current_question.return_value = queued_question
                    mock_manager.send_personal_message = AsyncMock(return_value=True)
                    mock_manager.send_personal_critical_message = AsyncMock(
                        return_value=True
                    )

                    asyncio.run(
                        routes.send_initial_session_state(
                            websocket, "SESSION123", "mobile", MagicMock()
                        )
                    )

    mock_manager.send_personal_message.assert_awaited_once()
    mock_manager.send_personal_critical_message.assert_awaited_once_with(
        "SESSION123",
        {"type": "question_started", "data": queued_question},
        websocket,
    )


def test_mobile_initial_state_includes_player_fair_play_status():
    websocket = MagicMock()

    with patch.object(routes, "get_game_session_state", return_value=None):
        with patch.object(routes, "resolve_session_game_type", return_value="trivia"):
            with patch.object(
                routes,
                "build_sync_state",
                return_value={"phase": "lobby"},
            ):
                with patch.object(routes, "manager") as mock_manager:
                    mock_manager.get_session_stats.return_value = {}
                    mock_manager.get_mobile_players.return_value = []
                    mock_manager.get_current_question.return_value = None
                    mock_manager.get_fair_play_status.return_value = {
                        "strike_count": 2,
                        "max_strikes": 3,
                    }
                    mock_manager.send_personal_message = AsyncMock(return_value=True)

                    asyncio.run(
                        routes.send_initial_session_state(
                            websocket,
                            "SESSION123",
                            "mobile",
                            MagicMock(),
                            player_id="P1",
                        )
                    )

    initial_state = mock_manager.send_personal_message.await_args.args[0]
    fair_play_status = initial_state["data"]["player_fair_play_status"]
    assert fair_play_status["player_id"] == "P1"
    assert fair_play_status["strike_count"] == 2
    assert (
        initial_state["data"]["authoritative_state"]["player_fair_play_status"]
        == fair_play_status
    )


def test_get_mobile_players_includes_connection_without_player_name():
    session_code = "NAMELESS"
    manager.active_connections[session_code] = {
        "ws1": {
            "client_type": "mobile",
            "player_id": "P2",
            "player_name": None,
            "player_photo": None,
            "connected_at": "2026-06-01T12:00:00",
            "player_answered": False,
            "connection_state": "connected",
        },
        "ws2": {
            "client_type": "mobile",
            "player_id": "P1",
            "player_name": "Alice",
            "player_photo": None,
            "connected_at": "2026-06-01T12:01:00",
            "player_answered": False,
            "connection_state": "connected",
        },
    }

    try:
        players = manager.get_mobile_players(session_code)
    finally:
        manager.active_connections.pop(session_code, None)

    assert {player["player_id"] for player in players} == {"P1", "P2"}
    nameless_player = next(player for player in players if player["player_id"] == "P2")
    assert nameless_player["player_name"] == "P2"


def test_get_mobile_players_includes_fair_play_status():
    session_code = "FAIRROSTER"
    manager.active_connections[session_code] = {
        "ws1": {
            "client_type": "mobile",
            "player_id": "P1",
            "player_name": "Alice",
            "player_photo": None,
            "connected_at": "2026-06-01T12:00:00",
            "player_answered": True,
            "connection_state": "connected",
        },
    }
    manager.update_fair_play_status(
        session_code,
        "P1",
        strike_count=2,
        max_strikes=3,
        is_frozen=True,
        frozen_question_id="Q1",
        is_kicked=False,
        answer_status="frozen",
    )

    try:
        players = manager.get_mobile_players(session_code)
    finally:
        manager.active_connections.pop(session_code, None)
        manager.fair_play_player_status.pop(session_code, None)

    assert players[0]["strike_count"] == 2
    assert players[0]["max_strikes"] == 3
    assert players[0]["is_frozen"] is True
    assert players[0]["frozen_question_id"] == "Q1"
    assert players[0]["answer_status"] == "frozen"


def test_reset_fair_play_freezes_clears_stale_roster_status():
    session_code = "FREEZERESET"
    manager.freeze_player_for_question(session_code, "P1", "Q1")
    manager.update_fair_play_status(
        session_code,
        "P1",
        strike_count=1,
        max_strikes=3,
        is_kicked=False,
    )

    try:
        manager.reset_fair_play_freezes_for_question(session_code, "Q2")
        status = manager.get_fair_play_status(session_code, "P1")
    finally:
        manager.fair_play_frozen_players.pop(session_code, None)
        manager.fair_play_player_status.pop(session_code, None)

    assert manager.is_player_frozen_for_question(session_code, "P1", "Q1") is False
    assert status["strike_count"] == 1
    assert status["is_frozen"] is False
    assert status["frozen_question_id"] is None
    assert status["answer_status"] is None


def test_disconnect_suppresses_player_left_during_pending_fair_play_focus_loss():
    session_code = "FOCUSLEAVE"
    websocket = SimpleNamespace()
    manager.active_connections[session_code] = {
        "ws1": {
            "client_type": "mobile",
            "websocket": websocket,
            "player_id": "P1",
            "player_name": "Alice",
        }
    }
    manager.websocket_registry["ws1"] = {
        "session_code": session_code,
        "websocket": websocket,
    }
    manager.set_session_phase(
        session_code,
        SessionPhase.QUESTION,
        current_question_id="Q1",
    )
    manager.record_pending_focus_loss(
        session_code,
        "P1",
        "Q1",
        "app_backgrounded",
        "2026-06-01T12:00:00Z",
    )

    with patch.object(manager, "_schedule_mobile_leave") as schedule_leave:
        try:
            manager.disconnect(websocket)
            still_present = "ws1" in manager.active_connections.get(session_code, {})
        finally:
            manager.active_connections.pop(session_code, None)
            manager.websocket_registry.pop("ws1", None)
            manager.session_phase_state.pop(session_code, None)
            manager.pending_focus_losses.pop(session_code, None)

    schedule_leave.assert_not_called()
    assert still_present is True


def test_roster_update_broadcasts_to_non_mobile_clients_only():
    session_code = "ROSTERHOST"
    web_socket = SimpleNamespace(send_text=AsyncMock())
    host_socket = SimpleNamespace(send_text=AsyncMock())
    mobile_socket = SimpleNamespace(send_text=AsyncMock())
    manager.active_connections[session_code] = {
        "web": {
            "client_type": "web",
            "websocket": web_socket,
            "player_name": None,
        },
        "host": {
            "client_type": "host",
            "websocket": host_socket,
            "player_name": None,
        },
        "mobile": {
            "client_type": "mobile",
            "websocket": mobile_socket,
            "player_id": "P1",
            "player_name": "Alice",
            "connected_at": "2026-06-01T12:00:00",
            "connection_confirmed": True,
        },
    }

    try:
        asyncio.run(manager.broadcast_player_roster_update(session_code))
    finally:
        manager.active_connections.pop(session_code, None)

    web_socket.send_text.assert_awaited_once()
    host_socket.send_text.assert_awaited_once()
    mobile_socket.send_text.assert_not_awaited()


def test_roster_update_uses_one_shared_presence_snapshot():
    session_code = "ROSTERSHARED"
    shared_presence = [
        {
            "client_type": "web",
            "connection_confirmed": True,
            "connected_at": "2026-06-01T12:00:00",
        },
        {
            "client_type": "mobile",
            "connection_confirmed": True,
            "player_id": "P1",
            "player_name": "Alice",
            "player_photo": None,
            "connected_at": "2026-06-01T12:00:01",
        },
    ]

    with patch.object(
        manager,
        "_shared_presence_metadata",
        return_value=shared_presence,
    ) as shared_snapshot:
        with patch.object(manager, "broadcast_to_session", AsyncMock()) as broadcast:
            asyncio.run(manager.broadcast_player_roster_update(session_code))

    shared_snapshot.assert_called_once_with(session_code)
    roster_message = broadcast.await_args.args[1]
    assert roster_message["data"]["connected_players"][0]["player_name"] == "Alice"
    assert roster_message["data"]["connection_stats"]["web_clients"] == 1
    assert roster_message["data"]["connection_stats"]["mobile_clients"] == 1


def test_shared_presence_cleanup_removes_expired_metadata():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"
    presence_key, meta_key = manager._presence_keys(session_code)
    expired_member = manager._presence_member("ws_expired")
    active_member = manager._presence_member("ws_active")
    fake_redis.zsets[presence_key] = {
        expired_member: 100,
        active_member: 250,
    }
    fake_redis.hashes[meta_key] = {
        expired_member: json.dumps(
            {
                "session_code": session_code,
                "ws_id": "ws_expired",
                "client_type": "mobile",
                "player_id": "P1",
            }
        ),
        active_member: json.dumps(
            {
                "session_code": session_code,
                "ws_id": "ws_active",
                "client_type": "mobile",
                "player_id": "P2",
            }
        ),
    }

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        with patch("app.websockets.manager.time.time", return_value=150):
            metadata = manager._shared_presence_metadata(session_code)

    assert metadata == [
        {
            "session_code": session_code,
            "ws_id": "ws_active",
            "client_type": "mobile",
            "player_id": "P2",
        }
    ]
    assert expired_member not in fake_redis.zsets[presence_key]
    assert expired_member not in fake_redis.hashes[meta_key]
    assert active_member in fake_redis.zsets[presence_key]
    assert active_member in fake_redis.hashes[meta_key]


def test_scheduled_roster_update_debounces_burst_requests():
    session_code = "ROSTERDEBOUNCE"

    async def run_test():
        with patch.object(manager, "broadcast_player_roster_update", AsyncMock()):
            try:
                await manager.schedule_player_roster_update(session_code, 0.01)
                await manager.schedule_player_roster_update(session_code, 0.01)
                await manager.schedule_player_roster_update(session_code, 0.01)
                task = manager.roster_update_tasks[session_code]
                await asyncio.wait_for(task, timeout=1)
                manager.broadcast_player_roster_update.assert_awaited_once_with(
                    session_code
                )
            finally:
                task = manager.roster_update_tasks.pop(session_code, None)
                if task and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    asyncio.run(run_test())


def test_trivia_answer_submission_runs_db_work_in_thread():
    handler = game_handlers.TriviaGameHandler("SESSION123")
    result = {
        "game_state": {
            "waiting_for_players": True,
            "playersAnswered": 1,
        }
    }

    async def run_test():
        with patch.object(game_handlers, "manager") as mock_manager:
            mock_manager.broadcast_to_session = AsyncMock()
            mock_manager.send_personal_message = AsyncMock()
            mock_manager.get_answered_count.return_value = 1
            mock_manager.get_session_connections.return_value = {}
            with patch.object(
                game_handlers.asyncio,
                "to_thread",
                new_callable=AsyncMock,
                return_value=(result, "Alice"),
            ) as to_thread:
                await handler.handle_player_answer(
                    "P1",
                    "A",
                    "Q1",
                    MagicMock(),
                )

        to_thread.assert_awaited_once_with(
            handler._submit_answer_in_thread_session,
            "P1",
            "Q1",
            "A",
        )
        mock_manager.set_player_answered.assert_called_once_with(
            "SESSION123",
            "P1",
            True,
        )
        assert mock_manager.broadcast_to_session.await_count == 2

    asyncio.run(run_test())


def test_mobile_current_question_payload_rebuilds_missing_queue_from_db():
    question = {
        "question_id": "Q1",
        "question": "Ready?",
        "display_options": ["A", "B"],
    }

    with patch.object(routes, "manager") as mock_manager:
        mock_manager.get_current_question.return_value = None
        mock_manager.get_session_phase_state.return_value = {
            "phase": "question",
            "current_question_id": "Q1",
            "start_at": "2026-06-01T12:00:00Z",
            "question_expires_at": "2026-06-01T12:00:15Z",
            "question_duration_ms": 15000,
            "server_time_ms": 123,
        }
        mock_manager.set_session_phase.return_value = {
            "phase": "question",
            "start_at": "2026-06-01T12:00:00Z",
            "question_expires_at": "2026-06-01T12:00:15Z",
            "question_duration_ms": 15000,
            "server_time_ms": 123,
        }
        with patch.object(
            routes,
            "get_current_question_details",
            return_value={"current_question": question},
        ):
            payload = routes.get_mobile_current_question_payload(
                "SESSION123", MagicMock(), "trivia"
            )

    assert payload["question_id"] == "Q1"
    assert payload["start_at"] == "2026-06-01T12:00:00Z"
    assert payload["expires_at"] == "2026-06-01T12:00:15Z"
    assert payload["duration_ms"] == 15000
    mock_manager.queue_question.assert_called_once_with("SESSION123", payload)


def test_mobile_current_question_payload_does_not_fallback_before_question_phase():
    with patch.object(routes, "manager") as mock_manager:
        mock_manager.get_current_question.return_value = None
        mock_manager.get_session_phase_state.return_value = {"phase": "countdown"}
        with patch.object(routes, "get_current_question_details") as get_details:
            payload = routes.get_mobile_current_question_payload(
                "SESSION123", MagicMock(), "trivia"
            )

    assert payload is None
    get_details.assert_not_called()
    mock_manager.queue_question.assert_not_called()


def test_update_session_settings_broadcasts_fair_play_settings():
    updated_state = SimpleNamespace(
        fair_play_enabled=False,
        max_fair_play_strikes=2,
    )

    with patch.object(
        routes,
        "update_fair_play_settings",
        return_value=updated_state,
    ) as update_settings:
        with patch.object(routes, "manager") as mock_manager:
            mock_manager.broadcast_to_session = AsyncMock()

            asyncio.run(
                routes.handle_update_session_settings(
                    "SESSION123",
                    {
                        "cheat_detection_enabled": "false",
                        "max_cheat_strikes": 2,
                    },
                    MagicMock(),
                )
            )

    update_settings.assert_called_once_with(
        update_settings.call_args.args[0],
        "SESSION123",
        fair_play_enabled=False,
        max_fair_play_strikes=2,
    )
    broadcast_payload = mock_manager.broadcast_to_session.await_args.args[1]
    assert broadcast_payload["type"] == "fair_play_settings_updated"
    assert broadcast_payload["data"]["fair_play_enabled"] is False
    assert broadcast_payload["data"]["max_fair_play_strikes"] == 2


def test_mobile_disconnect_during_fair_play_starts_focus_loss_grace():
    game_state = SimpleNamespace(
        fair_play_enabled=True,
        max_fair_play_strikes=3,
    )

    with patch.object(routes, "get_game_session_state", return_value=game_state):
        with patch.object(routes, "is_player_kicked", return_value=False):
            with patch.object(routes, "manager") as mock_manager:
                mock_manager._player_task_key.return_value = "SESSION123:P1"
                mock_manager.intentional_leaves = set()
                mock_manager.get_session_phase_state.return_value = {
                    "phase": "question",
                    "current_question_id": "Q1",
                }
                mock_manager.get_pending_focus_loss.return_value = None
                mock_manager.record_pending_focus_loss.return_value = {
                    "session_code": "SESSION123",
                    "player_id": "P1",
                    "question_id": "Q1",
                }
                with patch.object(
                    routes,
                    "finalize_focus_loss_after_grace",
                    new=MagicMock(return_value="task"),
                ) as finalize:
                    with patch.object(routes.asyncio, "create_task") as create_task:
                        asyncio.run(
                            routes.handle_mobile_disconnect_during_fair_play(
                                "SESSION123",
                                "P1",
                                MagicMock(),
                            )
                        )

    mock_manager.record_pending_focus_loss.assert_called_once()
    kwargs = mock_manager.record_pending_focus_loss.call_args.kwargs
    assert kwargs["reason"] == "mobile_disconnected_during_question"
    assert kwargs["question_id"] == "Q1"
    finalize.assert_called_once_with(
        session_code="SESSION123",
        player_id="P1",
        question_id="Q1",
        lost_at=kwargs["lost_at"],
    )
    create_task.assert_called_once_with("task")


def test_fair_play_status_payload_uses_database_record():
    game_state = SimpleNamespace(
        current_question_id="Q1",
        max_fair_play_strikes=3,
    )
    record = SimpleNamespace(strike_count=3, is_kicked=True)

    with patch.object(routes, "get_game_session_state", return_value=game_state):
        with patch.object(routes, "get_fair_play_record", return_value=record):
            with patch.object(
                routes, "has_focus_violation_for_question", return_value=True
            ):
                status = routes.build_player_fair_play_status(
                    MagicMock(), "SESSION123", "P1"
                )

    assert status["player_id"] == "P1"
    assert status["strike_count"] == 3
    assert status["max_strikes"] == 3
    assert status["is_kicked"] is True
    assert status["is_frozen"] is True
    assert status["answer_status"] == "kicked"
    assert status["reason"] == "fair_play_strikes"


def test_focus_violation_records_strike_and_freezes_player():
    websocket = MagicMock()
    game_state = SimpleNamespace(
        fair_play_enabled=True,
        max_fair_play_strikes=3,
    )
    record = SimpleNamespace(strike_count=1, is_kicked=False)
    violation = SimpleNamespace(id="V1")

    with patch.object(routes, "get_game_session_state", return_value=game_state):
        with patch.object(
            routes,
            "record_focus_violation",
            return_value=(record, violation, True),
        ) as record_violation:
            with patch.object(
                routes,
                "check_and_advance_game",
                return_value={"playersAnswered": 1, "waiting_for_players": True},
            ) as check_and_advance:
                with patch.object(routes, "manager") as mock_manager:
                    player_socket = MagicMock()
                    mock_manager.get_session_phase_state.return_value = {
                        "phase": "question",
                        "current_question_id": "Q1",
                    }
                    mock_manager.get_player_name_from_websocket.return_value = "Player"
                    mock_manager.get_player_connections.return_value = {
                        "ws1": {"websocket": player_socket}
                    }
                    mock_manager.broadcast_to_session = AsyncMock()
                    mock_manager.send_personal_critical_message = AsyncMock()

                    asyncio.run(
                        routes.handle_focus_violation(
                            websocket=websocket,
                            session_code="SESSION123",
                            player_id="P1",
                            data={
                                "question_id": "Q1",
                                "reason": "app_backgrounded",
                                "occurred_at": "2026-06-01T12:00:00Z",
                            },
                            db=MagicMock(),
                        )
                    )

    record_violation.assert_called_once()
    check_and_advance.assert_called_once()
    mock_manager.freeze_player_for_question.assert_called_once_with(
        "SESSION123", "P1", "Q1"
    )
    mock_manager.set_player_answered.assert_called_once_with("SESSION123", "P1", True)
    mock_manager.update_fair_play_status.assert_called_once_with(
        "SESSION123",
        "P1",
        strike_count=1,
        max_strikes=3,
        is_frozen=True,
        frozen_question_id="Q1",
        is_kicked=False,
        reason="app_backgrounded",
        fair_play_reason="app_backgrounded",
        answer_status="frozen",
    )
    broadcast_types = [
        call.args[1]["type"]
        for call in mock_manager.broadcast_to_session.await_args_list
    ]
    assert broadcast_types == [
        "player_flagged",
        "fair_play_status_update",
        "player_answered",
        "game_status_update",
    ]
    player_answered_payload = mock_manager.broadcast_to_session.await_args_list[2].args[
        1
    ]["data"]
    assert player_answered_payload["answer_status"] == "frozen"
    assert player_answered_payload["answered_current"] is True
    mock_manager.send_personal_critical_message.assert_awaited_once_with(
        "SESSION123",
        {
            "type": "fair_play_status_update",
            "data": mock_manager.broadcast_to_session.await_args_list[1].args[1][
                "data"
            ],
        },
        player_socket,
    )


def test_focus_violation_delays_progression_after_fair_play_kick():
    websocket = MagicMock()
    db = MagicMock()
    game_state = SimpleNamespace(
        fair_play_enabled=True,
        max_fair_play_strikes=3,
    )
    record = SimpleNamespace(strike_count=3, is_kicked=True)
    violation = SimpleNamespace(id="V1")

    with patch.object(routes, "get_game_session_state", return_value=game_state):
        with patch.object(
            routes,
            "record_focus_violation",
            return_value=(record, violation, False),
        ):
            with patch.object(routes, "manager") as mock_manager:
                mock_manager.get_session_phase_state.return_value = {
                    "phase": "question",
                    "current_question_id": "Q1",
                }
                mock_manager.get_player_name_from_websocket.return_value = "Player"
                mock_manager.get_player_connections.return_value = {}
                mock_manager.broadcast_to_session = AsyncMock()
                mock_manager.send_personal_critical_message = AsyncMock()
                with patch.object(
                    routes, "kick_player_for_fair_play", new_callable=AsyncMock
                ) as kick_player:
                    with patch.object(
                        routes,
                        "advance_after_fair_play_if_ready",
                        new_callable=AsyncMock,
                    ) as advance:
                        with patch.object(
                            routes.asyncio, "sleep", new_callable=AsyncMock
                        ) as sleep:
                            asyncio.run(
                                routes.handle_focus_violation(
                                    websocket=websocket,
                                    session_code="SESSION123",
                                    player_id="P1",
                                    data={
                                        "question_id": "Q1",
                                        "reason": "app_backgrounded",
                                    },
                                    db=db,
                                )
                            )

    kick_player.assert_awaited_once_with("SESSION123", "P1", 3, db)
    sleep.assert_awaited_once_with(0.75)
    advance.assert_awaited_once_with(
        "SESSION123",
        "Q1",
        db,
        acting_player_id="P1",
    )


def test_fair_play_focus_lost_starts_backend_grace_period():
    websocket = MagicMock()
    game_state = SimpleNamespace(fair_play_enabled=True)

    with patch.object(routes, "get_game_session_state", return_value=game_state):
        with patch.object(routes, "manager") as mock_manager:
            mock_manager.get_session_phase_state.return_value = {
                "phase": "question",
                "current_question_id": "Q1",
            }
            mock_manager.record_pending_focus_loss.return_value = {
                "session_code": "SESSION123",
                "player_id": "P1",
                "question_id": "Q1",
                "reason": "app_backgrounded",
                "lost_at": "2026-06-01T12:00:00Z",
            }
            mock_manager.send_personal_message = AsyncMock()
            mock_manager.send_personal_critical_message = AsyncMock()
            with patch.object(
                routes,
                "finalize_focus_loss_after_grace",
                new=MagicMock(return_value="task"),
            ) as finalize:
                with patch.object(routes.asyncio, "create_task") as create_task:
                    asyncio.run(
                        routes.handle_fair_play_focus_lost(
                            websocket=websocket,
                            session_code="SESSION123",
                            player_id="P1",
                            data={
                                "question_id": "Q1",
                                "reason": "app_backgrounded",
                                "occurred_at": "2026-06-01T12:00:00Z",
                            },
                            db=MagicMock(),
                        )
                    )

    mock_manager.record_pending_focus_loss.assert_called_once_with(
        session_code="SESSION123",
        player_id="P1",
        question_id="Q1",
        reason="app_backgrounded",
        lost_at="2026-06-01T12:00:00Z",
    )
    finalize.assert_called_once_with(
        session_code="SESSION123",
        player_id="P1",
        question_id="Q1",
        lost_at="2026-06-01T12:00:00Z",
    )
    create_task.assert_called_once_with("task")
    sent_message = mock_manager.send_personal_message.await_args.args[0]
    assert sent_message["type"] == "fair_play_focus_grace_started"
    assert sent_message["data"]["grace_period_ms"] == routes.FAIR_PLAY_GRACE_PERIOD_MS


def test_fair_play_return_uses_client_returned_at_for_reconnect_delay():
    websocket = MagicMock()
    pending = {
        "session_code": "SESSION123",
        "player_id": "P1",
        "question_id": "Q1",
        "reason": "app_backgrounded",
        "lost_at": "2026-06-01T12:00:00Z",
    }

    class FakeDateTime:
        @staticmethod
        def fromisoformat(value):
            return datetime.fromisoformat(value)

        @staticmethod
        def now(tz=None):
            now = datetime.fromisoformat("2026-06-01T12:00:03+00:00")
            return now if tz else now.replace(tzinfo=None)

    with patch.object(routes, "datetime", FakeDateTime):
        with patch.object(routes, "manager") as mock_manager:
            mock_manager.get_pending_focus_loss.return_value = pending
            mock_manager.clear_pending_focus_loss.return_value = pending
            mock_manager.broadcast_player_roster_update = AsyncMock()
            mock_manager.send_personal_message = AsyncMock()
            with patch.object(
                routes,
                "resync_buzzer_ui_after_fair_play_return",
                new_callable=AsyncMock,
            ):
                with patch.object(
                    routes, "handle_focus_violation", new_callable=AsyncMock
                ) as focus_violation:
                    asyncio.run(
                        routes.handle_fair_play_focus_returned(
                            websocket=websocket,
                            session_code="SESSION123",
                            player_id="P1",
                            data={
                                "question_id": "Q1",
                                "returned_at": "2026-06-01T12:00:01Z",
                            },
                        )
                    )

    focus_violation.assert_not_awaited()
    mock_manager.clear_pending_focus_loss.assert_called_once_with("SESSION123", "P1")
    assert mock_manager.send_personal_message.await_count == 2


def test_fair_play_immediate_reasons_bypass_grace_period():
    game_state = SimpleNamespace(fair_play_enabled=True)

    for reason in routes.IMMEDIATE_FAIR_PLAY_VIOLATION_REASONS:
        websocket = MagicMock()

        with patch.object(routes, "get_game_session_state", return_value=game_state):
            with patch.object(routes, "manager") as mock_manager:
                mock_manager.get_session_phase_state.return_value = {
                    "phase": "question",
                    "current_question_id": "Q1",
                }
                mock_manager.record_pending_focus_loss = MagicMock()
                mock_manager.send_personal_message = AsyncMock()
                with patch.object(
                    routes, "handle_focus_violation", new_callable=AsyncMock
                ) as focus_violation:
                    asyncio.run(
                        routes.handle_fair_play_focus_lost(
                            websocket=websocket,
                            session_code="SESSION123",
                            player_id="P1",
                            data={
                                "question_id": "Q1",
                                "reason": reason,
                                "occurred_at": "2026-06-01T12:00:00Z",
                            },
                            db=MagicMock(),
                        )
                    )

        focus_violation.assert_awaited_once()
        mock_manager.record_pending_focus_loss.assert_not_called()
        mock_manager.send_personal_message.assert_not_awaited()
        assert focus_violation.await_args.kwargs["data"]["reason"] == reason


def test_fair_play_window_violation_defaults_to_multi_window_reason():
    websocket = MagicMock()
    game_handler = MagicMock()

    with patch.object(
        routes, "handle_fair_play_focus_lost", new_callable=AsyncMock
    ) as focus_lost:
        asyncio.run(
            routes.handle_websocket_message(
                {
                    "type": "fair_play_window_violation",
                    "data": {
                        "question_id": "Q1",
                    },
                },
                websocket,
                "SESSION123",
                "mobile",
                "P1",
                "P1",
                game_handler,
                MagicMock(),
            )
        )

    focus_lost.assert_awaited_once()
    assert focus_lost.await_args.kwargs["data"]["question_id"] == "Q1"
    assert focus_lost.await_args.kwargs["data"]["reason"] == "multi_window_mode"


def test_kick_player_for_fair_play_sends_status_before_closing_socket():
    db = MagicMock()

    with patch.object(
        routes, "get_player_by_ID", return_value=SimpleNamespace(player_name="Alice")
    ):
        with patch.object(routes, "manager") as mock_manager:
            mock_manager.disconnect_player_everywhere = AsyncMock()
            mock_manager.broadcast_to_session = AsyncMock()
            mock_manager.broadcast_player_roster_update = AsyncMock()
            asyncio.run(
                routes.kick_player_for_fair_play(
                    "SESSION123",
                    "P1",
                    3,
                    db,
                )
            )

    disconnect_kwargs = mock_manager.disconnect_player_everywhere.await_args.kwargs
    disconnect_messages = disconnect_kwargs["messages"]
    assert [message["type"] for message in disconnect_messages] == [
        "fair_play_status_update",
        "kicked_from_session",
    ]
    fair_play_payload = disconnect_messages[0]["data"]
    kicked_payload = disconnect_messages[1]["data"]
    assert fair_play_payload["is_kicked"] is True
    assert fair_play_payload["strike_count"] == 3
    assert fair_play_payload["player_name"] == "Alice"
    assert kicked_payload["is_kicked"] is True
    assert kicked_payload["player_name"] == "Alice"
    assert disconnect_kwargs["close_code"] == 4003
    assert disconnect_kwargs["reason"] == "Removed after Fair Play strikes"
    broadcast_kwargs = mock_manager.broadcast_to_session.await_args.kwargs
    assert "exclude_client_types" not in broadcast_kwargs
    kicked_broadcast = mock_manager.broadcast_to_session.await_args.args[1]["data"]
    assert kicked_broadcast["player_name"] == "Alice"


def test_redis_bus_uses_configured_namespace_for_default_channel():
    with patch.dict(
        os.environ,
        {"REDIS_NAMESPACE": "phun:test", "WS_REDIS_CHANNEL": ""},
        clear=False,
    ):
        bus = redis_bus.RedisWebSocketBus()

    assert bus.namespace == "phun:test"
    assert bus.channel == "phun:test:ws:events"
    assert bus.key("session", "SESSION123", "presence") == (
        "phun:test:session:SESSION123:presence"
    )


def test_redis_bus_rejects_malformed_events():
    bus = redis_bus.RedisWebSocketBus()

    assert bus._validate_event(
        {
            "version": 1,
            "kind": "session_broadcast",
            "session_code": "SESSION123",
            "message": {"type": "roster_update"},
        }
    )
    assert not bus._validate_event(
        {
            "version": 2,
            "kind": "session_broadcast",
            "session_code": "SESSION123",
            "message": {"type": "roster_update"},
        }
    )
    assert not bus._validate_event(
        {
            "version": 1,
            "kind": "player_message",
            "session_code": "SESSION123",
            "message": {"type": "answer_result"},
        }
    )
    assert not bus._validate_event(
        {
            "version": 1,
            "kind": "unknown",
            "session_code": "SESSION123",
        }
    )
    assert not bus._validate_event(
        {
            "version": 1,
            "kind": "session_broadcast",
            "session_code": "SESSION123",
            "message": {"type": "debug_shell"},
        }
    )
    assert bus._validate_event(
        {
            "version": 1,
            "kind": "disconnect_player",
            "session_code": "SESSION123",
            "player_id": "P1",
            "messages": [{"type": "kicked_from_session"}],
        }
    )


def test_redis_bus_dispatches_different_sessions_independently():
    async def run_test():
        bus = redis_bus.RedisWebSocketBus()
        bus.dispatch_queue_idle_seconds = 60
        started_slow = asyncio.Event()
        release_slow = asyncio.Event()
        handled = []

        async def dispatcher(event):
            handled.append((event["session_code"], event["message"]["type"]))
            if event["session_code"] == "SLOW01":
                started_slow.set()
                await release_slow.wait()

        bus._dispatcher = dispatcher

        await bus._enqueue_event(
            {
                "version": 1,
                "kind": "session_broadcast",
                "session_code": "SLOW01",
                "message": {"type": "first"},
            }
        )
        await asyncio.wait_for(started_slow.wait(), timeout=1)

        await bus._enqueue_event(
            {
                "version": 1,
                "kind": "session_broadcast",
                "session_code": "FAST01",
                "message": {"type": "second"},
            }
        )
        await asyncio.wait_for(bus._dispatch_queues["FAST01"].join(), timeout=1)
        assert ("FAST01", "second") in handled

        await bus._enqueue_event(
            {
                "version": 1,
                "kind": "session_broadcast",
                "session_code": "SLOW01",
                "message": {"type": "third"},
            }
        )
        await asyncio.sleep(0)
        assert handled == [("SLOW01", "first"), ("FAST01", "second")]

        release_slow.set()
        await asyncio.wait_for(bus._dispatch_queues["SLOW01"].join(), timeout=1)
        assert handled == [
            ("SLOW01", "first"),
            ("FAST01", "second"),
            ("SLOW01", "third"),
        ]
        await bus.close()

    asyncio.run(run_test())


def test_redis_bus_does_not_drop_control_events_when_session_queue_is_full():
    async def run_test():
        bus = redis_bus.RedisWebSocketBus()
        bus.dispatch_queue_maxsize = 1
        bus.dispatch_queue_idle_seconds = 60
        started_first = asyncio.Event()
        release_first = asyncio.Event()
        handled = []

        async def dispatcher(event):
            handled.append(event["kind"])
            if event["kind"] == "session_broadcast" and not started_first.is_set():
                started_first.set()
                await release_first.wait()

        bus._dispatcher = dispatcher

        try:
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "session_broadcast",
                    "session_code": "FULL01",
                    "message": {"type": "roster_update"},
                }
            )
            await asyncio.wait_for(started_first.wait(), timeout=1)

            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "session_broadcast",
                    "session_code": "FULL01",
                    "message": {"type": "beat_clock_state"},
                }
            )

            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "disconnect_player",
                    "session_code": "FULL01",
                    "player_id": "P1",
                    "messages": [{"type": "kicked_from_session"}],
                }
            )
            assert bus.dropped_control_event_count == 0

            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "session_broadcast",
                    "session_code": "FAST01",
                    "message": {"type": "roster_update"},
                }
            )
            await asyncio.wait_for(bus._dispatch_queues["FAST01"].join(), timeout=1)
            assert any(
                item == "session_broadcast"
                for item in handled
            )

            release_first.set()
            await asyncio.wait_for(bus._dispatch_queues["FULL01"].join(), timeout=1)
        finally:
            await bus.close()

        assert handled.count("session_broadcast") == 2
        assert handled.count("disconnect_player") == 1

    asyncio.run(run_test())


def test_redis_bus_reliable_backlog_preserves_distinct_control_events():
    async def run_test():
        bus = redis_bus.RedisWebSocketBus()
        bus.dispatch_queue_maxsize = 1
        bus.dispatch_queue_idle_seconds = 60
        started_first = asyncio.Event()
        release_first = asyncio.Event()
        handled = []

        async def dispatcher(event):
            handled.append(event["player_id"])
            if not started_first.is_set():
                started_first.set()
                await release_first.wait()

        bus._dispatcher = dispatcher

        try:
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "disconnect_player",
                    "session_code": "CTRL01",
                    "player_id": "P1",
                    "messages": [{"type": "kicked_from_session"}],
                }
            )
            await asyncio.wait_for(started_first.wait(), timeout=1)

            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "disconnect_player",
                    "session_code": "CTRL01",
                    "player_id": "P2",
                    "messages": [{"type": "kicked_from_session"}],
                }
            )
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "disconnect_player",
                    "session_code": "CTRL01",
                    "player_id": "P3",
                    "messages": [{"type": "kicked_from_session"}],
                }
            )

            assert bus.dropped_control_event_count == 0
            assert [
                event["player_id"]
                for event, _event_bytes in bus._reliable_backlogs["CTRL01"]
            ] == ["P3"]
            release_first.set()
            await asyncio.wait_for(bus._dispatch_queues["CTRL01"].join(), timeout=1)
        finally:
            await bus.close()

        assert handled == ["P1", "P2", "P3"]

    asyncio.run(run_test())


def test_redis_bus_does_not_drop_targeted_player_messages_as_snapshots():
    async def run_test():
        bus = redis_bus.RedisWebSocketBus()
        bus.dispatch_queue_maxsize = 1
        bus.dispatch_queue_idle_seconds = 60
        started_first = asyncio.Event()
        release_first = asyncio.Event()
        handled = []

        async def dispatcher(event):
            handled.append((event["kind"], event.get("player_id")))
            if not started_first.is_set():
                started_first.set()
                await release_first.wait()

        bus._dispatcher = dispatcher

        try:
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "session_broadcast",
                    "session_code": "TARGET01",
                    "message": {"type": "beat_clock_state"},
                }
            )
            await asyncio.wait_for(started_first.wait(), timeout=1)
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "player_message",
                    "session_code": "TARGET01",
                    "player_id": "P42",
                    "message": {"type": "beat_clock_answer_result"},
                }
            )
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "session_broadcast",
                    "session_code": "TARGET01",
                    "message": {"type": "roster_update"},
                }
            )

            release_first.set()
            await asyncio.wait_for(bus._dispatch_queues["TARGET01"].join(), timeout=1)
        finally:
            await bus.close()

        assert ("player_message", "P42") in handled
        assert bus.dropped_control_event_count == 0

    asyncio.run(run_test())


def test_redis_bus_reliable_backlog_enforces_byte_limits():
    async def run_test():
        bus = redis_bus.RedisWebSocketBus()
        bus.dispatch_queue_maxsize = 1
        bus.reliable_backlog_session_max_bytes = 120
        bus.reliable_backlog_total_max_bytes = 120
        bus.dispatch_queue_idle_seconds = 60
        started_first = asyncio.Event()
        release_first = asyncio.Event()

        async def dispatcher(event):
            if not started_first.is_set():
                started_first.set()
                await release_first.wait()

        bus._dispatcher = dispatcher

        try:
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "session_broadcast",
                    "session_code": "BYTES01",
                    "message": {"type": "beat_clock_state"},
                }
            )
            await asyncio.wait_for(started_first.wait(), timeout=1)
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "player_message",
                    "session_code": "BYTES01",
                    "player_id": "P1",
                    "message": {
                        "type": "beat_clock_question",
                        "data": {"question_id": "BTC001"},
                    },
                }
            )
            await bus._enqueue_event(
                {
                    "version": 1,
                    "kind": "player_message",
                    "session_code": "BYTES01",
                    "player_id": "P1",
                    "message": {
                        "type": "beat_clock_question",
                        "data": {"question_id": "BTC001", "blob": "x" * 200},
                    },
                }
            )
        finally:
            release_first.set()
            await bus.close()

        assert bus.dropped_event_count == 1
        assert "BYTES01" not in bus._reliable_backlogs

    asyncio.run(run_test())


def test_local_session_broadcast_uses_per_socket_queues_for_slow_clients():
    class FakeWebSocket:
        def __init__(self, name, block_event=None, release_event=None):
            self.name = name
            self.sent = []
            self.block_event = block_event
            self.release_event = release_event

        async def send_text(self, payload):
            self.sent.append(payload)
            if self.block_event and self.release_event:
                self.block_event.set()
                await self.release_event.wait()

    async def run_test():
        session_code = "QUEUE01"
        slow_started = asyncio.Event()
        release_slow = asyncio.Event()
        slow_ws = FakeWebSocket("slow", slow_started, release_slow)
        fast_ws = FakeWebSocket("fast")
        old_queue_maxsize = manager.outbound_queue_maxsize

        manager.outbound_queue_maxsize = 10
        manager.active_connections[session_code] = {
            "ws_slow": {
                "websocket": slow_ws,
                "client_type": "web",
                "ws_id": "ws_slow",
            },
            "ws_fast": {
                "websocket": fast_ws,
                "client_type": "web",
                "ws_id": "ws_fast",
            },
        }
        manager.websocket_registry["ws_slow"] = {
            "session_code": session_code,
            "websocket": slow_ws,
        }
        manager.websocket_registry["ws_fast"] = {
            "session_code": session_code,
            "websocket": fast_ws,
        }
        manager.websocket_to_ws_id[id(slow_ws)] = "ws_slow"
        manager.websocket_to_ws_id[id(fast_ws)] = "ws_fast"

        try:
            broadcast_task = asyncio.create_task(
                manager._broadcast_local_to_session(
                    session_code,
                    {"type": "roster_update", "data": {"ok": True}},
                )
            )
            await asyncio.wait_for(slow_started.wait(), timeout=1)
            await asyncio.wait_for(broadcast_task, timeout=0.2)
            assert fast_ws.sent
        finally:
            release_slow.set()
            for connection_info in manager.active_connections.get(
                session_code,
                {},
            ).values():
                sender_task = connection_info.get("outbound_sender_task")
                if sender_task and not sender_task.done():
                    sender_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender_task
            manager.active_connections.pop(session_code, None)
            manager.websocket_registry.pop("ws_slow", None)
            manager.websocket_registry.pop("ws_fast", None)
            manager.websocket_to_ws_id.pop(id(slow_ws), None)
            manager.websocket_to_ws_id.pop(id(fast_ws), None)
            manager.outbound_queue_maxsize = old_queue_maxsize

    asyncio.run(run_test())


def test_personal_messages_share_socket_sender_queue_with_broadcasts():
    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.first_send_started = asyncio.Event()
            self.release_first_send = asyncio.Event()

        async def send_text(self, payload):
            self.sent.append(payload)
            if len(self.sent) == 1:
                self.first_send_started.set()
                await self.release_first_send.wait()

    async def run_test():
        session_code = "QUEUE02"
        websocket = FakeWebSocket()
        manager.active_connections[session_code] = {
            "ws_one": {
                "websocket": websocket,
                "client_type": "mobile",
                "ws_id": "ws_one",
                "player_id": "P1",
            }
        }
        manager.websocket_registry["ws_one"] = {
            "session_code": session_code,
            "websocket": websocket,
        }
        manager.websocket_to_ws_id[id(websocket)] = "ws_one"

        try:
            broadcast_task = asyncio.create_task(
                manager._broadcast_local_to_session(
                    session_code,
                    {"type": "roster_update", "data": {"step": 1}},
                )
            )
            await asyncio.wait_for(websocket.first_send_started.wait(), timeout=1)
            personal_task = asyncio.create_task(
                manager.send_personal_message(
                    {"type": "sync_state", "data": {"step": 2}},
                    websocket,
                    retries=0,
                )
            )
            await asyncio.sleep(0.05)
            assert len(websocket.sent) == 1
            websocket.release_first_send.set()
            await asyncio.wait_for(broadcast_task, timeout=1)
            assert await asyncio.wait_for(personal_task, timeout=1)
            assert len(websocket.sent) == 2
        finally:
            websocket.release_first_send.set()
            for connection_info in manager.active_connections.get(
                session_code,
                {},
            ).values():
                sender_task = connection_info.get("outbound_sender_task")
                if sender_task and not sender_task.done():
                    sender_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender_task
            manager.active_connections.pop(session_code, None)
            manager.websocket_registry.pop("ws_one", None)
            manager.websocket_to_ws_id.pop(id(websocket), None)

    asyncio.run(run_test())


def test_pending_personal_message_future_fails_when_sender_is_cancelled():
    class FakeWebSocket:
        def __init__(self):
            self.first_send_started = asyncio.Event()
            self.release_first_send = asyncio.Event()

        async def send_text(self, payload):
            if not self.first_send_started.is_set():
                self.first_send_started.set()
                await self.release_first_send.wait()

    async def run_test():
        session_code = "QUEUE03"
        websocket = FakeWebSocket()
        manager.active_connections[session_code] = {
            "ws_one": {
                "websocket": websocket,
                "client_type": "mobile",
                "ws_id": "ws_one",
                "player_id": "P1",
            }
        }
        manager.websocket_registry["ws_one"] = {
            "session_code": session_code,
            "websocket": websocket,
        }
        manager.websocket_to_ws_id[id(websocket)] = "ws_one"

        try:
            first_task = asyncio.create_task(
                manager.send_personal_message(
                    {"type": "sync_state", "data": {"step": 1}},
                    websocket,
                    retries=0,
                )
            )
            await asyncio.wait_for(websocket.first_send_started.wait(), timeout=1)
            second_task = asyncio.create_task(
                manager.send_personal_message(
                    {"type": "sync_state", "data": {"step": 2}},
                    websocket,
                    retries=0,
                )
            )
            await asyncio.sleep(0)

            sender_task = manager.active_connections[session_code]["ws_one"][
                "outbound_sender_task"
            ]
            sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender_task

            assert await asyncio.wait_for(second_task, timeout=1) is False
            assert await asyncio.wait_for(first_task, timeout=1) is False
        finally:
            websocket.release_first_send.set()
            manager.active_connections.pop(session_code, None)
            manager.websocket_registry.pop("ws_one", None)
            manager.websocket_to_ws_id.pop(id(websocket), None)

    asyncio.run(run_test())


def test_noncritical_outbound_overflow_does_not_evict_critical_message():
    async def run_test():
        session_code = "QUEUE04"
        ws_id = "ws_one"
        old_queue_maxsize = manager.outbound_queue_maxsize
        manager.outbound_queue_maxsize = 1
        connection_info = {
            "websocket": MagicMock(),
            "client_type": "mobile",
            "ws_id": ws_id,
            "player_id": "P1",
        }
        try:
            queue = asyncio.Queue(maxsize=1)
            connection_info["outbound_queue"] = queue
            connection_info["outbound_sender_task"] = asyncio.current_task()

            critical_future = asyncio.get_running_loop().create_future()
            queue.put_nowait(
                OutboundQueueItem(
                    payload='{"type":"question_started"}',
                    sent_future=critical_future,
                    critical=True,
                    replaceable=False,
                )
            )
            queued = await manager._enqueue_broadcast_payload(
                session_code,
                ws_id,
                connection_info,
                '{"type":"ping"}',
                critical=False,
                replaceable=True,
                coalesce_key="ping",
            )

            assert queued is False
            assert queue.qsize() == 1
            remaining = queue.get_nowait()
            queue.task_done()
            assert isinstance(remaining, OutboundQueueItem)
            assert remaining.critical is True
            assert not critical_future.done()
        finally:
            manager._fail_pending_outbound_queue(connection_info["outbound_queue"])
            manager.outbound_queue_maxsize = old_queue_maxsize

    asyncio.run(run_test())


def test_personal_critical_ack_ids_are_target_specific():
    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

    async def run_test():
        session_code = "ACKP01"
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        manager.active_connections[session_code] = {
            "ws_p1": {
                "websocket": ws1,
                "client_type": "mobile",
                "ws_id": "ws_p1",
                "player_id": "P1",
                "connection_generation": "worker:ws_p1",
            },
            "ws_p2": {
                "websocket": ws2,
                "client_type": "mobile",
                "ws_id": "ws_p2",
                "player_id": "P2",
                "connection_generation": "worker:ws_p2",
            },
        }
        manager.websocket_registry["ws_p1"] = {
            "session_code": session_code,
            "websocket": ws1,
        }
        manager.websocket_registry["ws_p2"] = {
            "session_code": session_code,
            "websocket": ws2,
        }
        manager.websocket_to_ws_id[id(ws1)] = "ws_p1"
        manager.websocket_to_ws_id[id(ws2)] = "ws_p2"

        try:
            await manager.send_personal_critical_message(
                session_code,
                {
                    "type": "beat_clock_answer_result",
                    "data": {"question_id": "BTC001", "score": 8},
                },
                ws1,
            )
            await manager.send_personal_critical_message(
                session_code,
                {
                    "type": "beat_clock_answer_result",
                    "data": {"question_id": "BTC001", "score": 3},
                },
                ws2,
            )

            event_ids = list(manager.pending_acks)
            assert len(event_ids) == 2
            assert event_ids[0] != event_ids[1]
            assert any(event_id.endswith("worker:ws_p1") for event_id in event_ids)
            assert any(event_id.endswith("worker:ws_p2") for event_id in event_ids)
        finally:
            for event_id in list(manager.pending_acks):
                if event_id.startswith(session_code):
                    manager.pending_acks.pop(event_id, None)
                    retry_task = manager.ack_retry_tasks.pop(event_id, None)
                    if retry_task and not retry_task.done():
                        retry_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await retry_task
            for connection_info in manager.active_connections.get(
                session_code,
                {},
            ).values():
                sender_task = connection_info.get("outbound_sender_task")
                if sender_task and not sender_task.done():
                    sender_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender_task
            manager.active_connections.pop(session_code, None)
            manager.websocket_registry.pop("ws_p1", None)
            manager.websocket_registry.pop("ws_p2", None)
            manager.websocket_to_ws_id.pop(id(ws1), None)
            manager.websocket_to_ws_id.pop(id(ws2), None)

    asyncio.run(run_test())


def test_ack_retry_uses_per_target_personalized_payload():
    async def run_test():
        session_code = "ACKP02"
        event_id = "shared-event"
        ws1 = MagicMock()
        ws2 = MagicMock()
        manager.active_connections[session_code] = {
            "ws_p1": {
                "websocket": ws1,
                "client_type": "mobile",
                "ws_id": "ws_p1",
                "player_id": "P1",
            },
            "ws_p2": {
                "websocket": ws2,
                "client_type": "mobile",
                "ws_id": "ws_p2",
                "player_id": "P2",
            },
        }
        manager._track_ack_target(
            event_id,
            session_code,
            {
                "type": "beat_clock_answer_result",
                "event_id": event_id,
                "data": {"question_id": "BTC001", "score": 8},
            },
            "ws_p1",
            manager.active_connections[session_code]["ws_p1"],
        )
        manager._track_ack_target(
            event_id,
            session_code,
            {
                "type": "beat_clock_answer_result",
                "event_id": event_id,
                "data": {"question_id": "BTC001", "score": 3},
            },
            "ws_p2",
            manager.active_connections[session_code]["ws_p2"],
        )

        sent_messages = []

        async def fake_send(message, websocket, **kwargs):
            sent_messages.append((websocket, message))
            return True

        try:
            with patch("app.websockets.manager.asyncio.sleep", new=AsyncMock(
                side_effect=[None, asyncio.CancelledError()]
            )), patch.object(
                manager,
                "send_personal_message",
                side_effect=fake_send,
            ):
                with contextlib.suppress(asyncio.CancelledError):
                    await manager._retry_unacked_event(event_id)
        finally:
            manager.pending_acks.pop(event_id, None)
            manager.ack_retry_tasks.pop(event_id, None)
            manager.active_connections.pop(session_code, None)

        scores_by_socket = {
            websocket: message["data"]["score"]
            for websocket, message in sent_messages
        }
        assert scores_by_socket[ws1] == 8
        assert scores_by_socket[ws2] == 3

    asyncio.run(run_test())


def test_ack_retry_task_is_deduplicated_per_event_id():
    async def run_test():
        event_id = "dedup-event"
        manager.pending_acks[event_id] = {
            "event_id": event_id,
            "session_code": "ACKP03",
            "message": {"type": "question_started", "event_id": event_id},
            "created_at": datetime.now(UTC).isoformat(),
            "resend_count": 0,
            "targets": {},
        }

        try:
            manager._schedule_ack_retry(event_id)
            first_task = manager.ack_retry_tasks.get(event_id)
            manager._schedule_ack_retry(event_id)
            second_task = manager.ack_retry_tasks.get(event_id)

            assert first_task is second_task
        finally:
            manager.pending_acks.pop(event_id, None)
            retry_task = manager.ack_retry_tasks.pop(event_id, None)
            if retry_task and not retry_task.done():
                retry_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retry_task

    asyncio.run(run_test())


def test_redis_player_message_dispatch_enqueues_without_waiting_for_socket_send():
    class SlowWebSocket:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send_text(self, payload):
            self.started.set()
            await self.release.wait()

    async def run_test():
        session_code = "DISP01"
        websocket = SlowWebSocket()
        manager.active_connections[session_code] = {
            "ws_p1": {
                "websocket": websocket,
                "client_type": "mobile",
                "ws_id": "ws_p1",
                "player_id": "P1",
                "connection_generation": "worker:ws_p1",
            }
        }
        manager.websocket_registry["ws_p1"] = {
            "session_code": session_code,
            "websocket": websocket,
        }
        manager.websocket_to_ws_id[id(websocket)] = "ws_p1"
        manager.player_connection_index[(session_code, "P1")] = {"ws_p1"}

        try:
            await asyncio.wait_for(
                manager.dispatch_bus_event(
                    {
                        "kind": "player_message",
                        "session_code": session_code,
                        "player_id": "P1",
                        "critical": True,
                        "message": {
                            "type": "beat_clock_question",
                            "data": {"question_id": "BTC001"},
                        },
                    }
                ),
                timeout=0.2,
            )
            assert any(event_id.startswith(session_code) for event_id in manager.pending_acks)
        finally:
            websocket.release.set()
            for connection_info in manager.active_connections.get(
                session_code,
                {},
            ).values():
                sender_task = connection_info.get("outbound_sender_task")
                if sender_task and not sender_task.done():
                    sender_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender_task
            for event_id in list(manager.pending_acks):
                if event_id.startswith(session_code):
                    manager.pending_acks.pop(event_id, None)
                    retry_task = manager.ack_retry_tasks.pop(event_id, None)
                    if retry_task and not retry_task.done():
                        retry_task.cancel()
            manager.active_connections.pop(session_code, None)
            manager.websocket_registry.pop("ws_p1", None)
            manager.websocket_to_ws_id.pop(id(websocket), None)
            manager.player_connection_index.pop((session_code, "P1"), None)

    asyncio.run(run_test())


def test_beat_clock_answer_does_not_need_route_owned_db_session():
    handler = game_handlers.BeatTheClockGameHandler("SESSION123")

    assert (
        routes.websocket_message_needs_route_db(
            {
                "type": "submit_answer",
                "data": {"question_id": "BTC001", "answer": "A"},
            },
            "mobile",
            handler,
        )
        is False
    )


def test_beat_clock_submit_answer_with_no_route_db_reaches_handler():
    async def run_test():
        websocket = MagicMock()
        handler = SimpleNamespace(
            game_type=game_modes.BEAT_THE_CLOCK_GAME_TYPE,
            handle_player_answer=AsyncMock(),
        )

        with patch.object(
            routes.manager,
            "get_beat_clock_state_for_player",
            return_value={
                "active": True,
                "ends_at_dt": routes.utc_now() + timedelta(seconds=30),
            },
        ), patch.object(
            routes.manager,
            "is_player_frozen_for_question",
            return_value=False,
        ), patch.object(
            routes,
            "is_player_kicked",
            side_effect=AssertionError("route must not call kicked check without db"),
        ):
            await routes.handle_websocket_message(
                {
                    "type": "submit_answer",
                    "data": {"question_id": "BTC001", "answer": "A"},
                },
                websocket,
                "SESSION123",
                "mobile",
                "P1",
                "P1",
                handler,
                None,
            )

        handler.handle_player_answer.assert_awaited_once_with(
            "P1",
            "A",
            "BTC001",
            None,
        )

    asyncio.run(run_test())


def test_load_connect_requires_player_fixture_for_multiple_mobile_clients():
    args = SimpleNamespace(
        players_file=None,
        mobile=True,
        clients=2,
        concurrency=1,
        ws_url="ws://example.invalid/ws",
        token="token",
        player_prefix="LOAD",
        hold_seconds=0,
    )

    with pytest.raises(ValueError, match="players-file"):
        asyncio.run(load_test_websocket.run_connect(args))


def test_fair_play_status_uses_per_player_redis_hash_fields():
    fake_redis = _FakeRedis()

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        manager.fair_play_player_status.pop("SESSION123", None)
        manager.update_fair_play_status("SESSION123", "P1", strike_count=1)
        manager.update_fair_play_status("SESSION123", "P2", strike_count=2)

        p1_status = manager.get_fair_play_status("SESSION123", "P1")
        p2_status = manager.get_fair_play_status("SESSION123", "P2")

    status_key = manager._fair_play_status_key("SESSION123")
    assert set(fake_redis.hashes[status_key]) == {"P1", "P2"}
    assert json.loads(fake_redis.hashes[status_key]["P1"])["strike_count"] == 1
    assert p1_status["strike_count"] == 1
    assert p2_status["strike_count"] == 2


def test_fair_play_statuses_read_shared_redis_hash_snapshot():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"
    status_key = manager._fair_play_status_key(session_code)
    fake_redis.hashes[status_key] = {
        "P1": json.dumps({"strike_count": 1, "is_kicked": False}),
        "P2": json.dumps({"strike_count": 3, "is_kicked": True}),
    }
    manager.fair_play_player_status[session_code] = {"LOCAL_ONLY": {"strike_count": 2}}

    try:
        with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
            statuses = manager.get_fair_play_statuses(session_code)
    finally:
        manager.fair_play_player_status.pop(session_code, None)

    assert statuses["P1"]["strike_count"] == 1
    assert statuses["P2"]["is_kicked"] is True
    assert statuses["LOCAL_ONLY"]["strike_count"] == 2
    assert fake_redis.hgetall_calls == [status_key]


def test_fair_play_freeze_reset_reads_per_player_redis_hash_fields():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        manager.fair_play_frozen_players.pop(session_code, None)
        manager.fair_play_player_status.pop(session_code, None)
        manager.freeze_player_for_question(session_code, "P1", "Q1")
        manager.freeze_player_for_question(session_code, "P2", "Q2")
        manager.fair_play_frozen_players.pop(session_code, None)

        manager.reset_fair_play_freezes_for_question(session_code, "Q2")

        frozen_key = manager._fair_play_frozen_key(session_code)
        assert fake_redis.hashes[frozen_key] == {"P2": "Q2"}
        assert manager.get_fair_play_status(session_code, "P1")["is_frozen"] is False
        assert manager.get_fair_play_status(session_code, "P2")["is_frozen"] is True


def test_beat_clock_player_state_read_avoids_full_player_hash_scan():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        manager.beat_clock_states.pop(session_code, None)
        manager.set_beat_clock_state(
            session_code,
            {
                "active": True,
                "duration_seconds": 60,
                "ends_at": "2026-07-10T12:00:00",
                "questions": ["Q1"],
                "players": {},
                "leaderboard": [],
            },
        )
        manager.update_beat_clock_player_state(
            session_code,
            "P1",
            {"current_question_id": "Q1", "answered_count": 0},
        )
        manager.update_beat_clock_player_state(
            session_code,
            "P2",
            {"current_question_id": "Q2", "answered_count": 1},
        )
        manager.beat_clock_states.pop(session_code, None)

        state = manager.get_beat_clock_state_for_player(session_code, "P1")

    assert state["active"] is True
    assert state["players"] == {
        "P1": {"current_question_id": "Q1", "answered_count": 0}
    }
    assert fake_redis.hget_calls == [(manager._beat_clock_keys(session_code)[1], "P1")]
    assert fake_redis.hgetall_calls == []


def test_beat_clock_meta_state_read_avoids_full_player_hash_scan():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        manager.beat_clock_states.pop(session_code, None)
        manager.set_beat_clock_state(
            session_code,
            {
                "active": True,
                "duration_seconds": 60,
                "ends_at": "2026-07-10T12:00:00",
                "questions": ["Q1"],
                "players": {},
                "leaderboard": [],
            },
        )
        manager.update_beat_clock_player_state(
            session_code,
            "P1",
            {"current_question_id": "Q1", "answered_count": 0},
        )
        manager.beat_clock_states.pop(session_code, None)

        state = manager.get_beat_clock_meta_state(session_code)

    assert state["active"] is True
    assert state["duration_seconds"] == 60
    assert fake_redis.hget_calls == []
    assert fake_redis.hgetall_calls == []


def test_beat_clock_leaderboard_uses_single_score_query_projection():
    handler = game_handlers.BeatTheClockGameHandler("SESSION123")
    db = MagicMock()
    projected_scores = [
        {
            "rank": 1,
            "player_id": "P1",
            "display_name": "Alice",
            "player_photo_url": "alice.jpg",
            "score": 4,
        },
        {
            "rank": 2,
            "player_id": "P2",
            "display_name": "Bob",
            "player_photo_url": None,
            "score": 2,
        },
    ]

    with patch.object(
        game_handlers,
        "get_session_score_leaderboard",
        return_value=projected_scores,
    ) as leaderboard_query:
        with patch.object(manager, "get_mobile_players") as get_mobile_players:
            with patch.object(
                game_handlers,
                "get_scores_by_session_and_player",
            ) as get_score:
                leaderboard = handler._leaderboard(db)

    leaderboard_query.assert_called_once_with(db, "SESSION123")
    get_mobile_players.assert_not_called()
    get_score.assert_not_called()
    assert leaderboard[0]["display_name"] == "Alice"
    assert leaderboard[0]["roster_player_id"] == game_handlers.make_roster_player_id(
        "SESSION123",
        "P1",
    )


def test_beat_clock_answer_updates_redis_projection_after_db_commit():
    events = []
    handler = game_handlers.BeatTheClockGameHandler("SESSION123")
    db = MagicMock()
    state = {
        "active": True,
        "duration_seconds": 60,
        "ends_at": "2026-07-10T12:00:00",
        "ends_at_dt": datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1),
        "players": {
            "P1": {
                "current_question_id": "Q1",
                "answered_count": 0,
                "correct_count": 0,
            }
        },
    }

    def update_player_state(session_code, player_id, player_state):
        events.append("redis_update")
        assert player_state["answered_count"] == 1
        assert player_state["correct_count"] == 1

    def process_answer(player_id, answer, question_id, player_state, state_context):
        events.extend(["response", "score", "commit"])
        return {
            "status": "accepted",
            "updated_player_state": {
                **player_state,
                "answered_count": 1,
                "correct_count": 1,
            },
            "payload": {
                "game_type": game_handlers.BEAT_THE_CLOCK_GAME_TYPE,
                "question_id": question_id,
                "is_correct": True,
                "score": 1,
                "answered_count": 1,
                "correct_count": 1,
                "duration_seconds": state_context["duration_seconds"],
                "ends_at": state_context["ends_at"],
                "server_time_ms": 1,
                "answer_match": {"method": "exact", "score": 1.0},
            },
        }

    async def run_test():
        with patch.object(
            manager,
            "get_beat_clock_state_for_player",
            return_value=state,
        ):
            with patch.object(
                handler,
                "_process_beat_clock_answer_in_thread",
                side_effect=process_answer,
            ):
                with patch.object(
                    manager,
                    "update_beat_clock_player_state",
                    side_effect=update_player_state,
                ):
                    with patch.object(
                        manager,
                        "send_message_to_player",
                        AsyncMock(),
                    ):
                        with patch.object(
                            handler,
                            "_send_question_to_player",
                            AsyncMock(return_value=True),
                        ):
                            with patch.object(
                                handler,
                                "schedule_state_broadcast",
                            ):
                                await handler.handle_player_answer(
                                    "P1",
                                    "A",
                                    "Q1",
                                    db,
                                )

    asyncio.run(run_test())

    assert events == ["response", "score", "commit", "redis_update"]


def test_beat_clock_state_broadcast_is_debounced_per_session():
    handler = game_handlers.BeatTheClockGameHandler("SESSION123")

    async def run_test():
        with patch.object(
            handler,
            "_broadcast_state_from_thread",
            AsyncMock(return_value={}),
        ) as broadcast:
            first_task = handler.schedule_state_broadcast(delay_seconds=0.01)
            second_task = handler.schedule_state_broadcast(delay_seconds=0.01)
            assert first_task is second_task
            await asyncio.wait_for(second_task, timeout=1)

        broadcast.assert_awaited_once()

    try:
        asyncio.run(run_test())
    finally:
        task = game_handlers.BeatTheClockGameHandler._state_broadcast_tasks.pop(
            "SESSION123",
            None,
        )
        if task and not task.done():
            task.cancel()


def test_beat_clock_finish_claim_is_single_winner_in_redis():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        first_claim = manager.claim_beat_clock_finish(session_code)
        second_claim = manager.claim_beat_clock_finish(session_code)

    assert first_claim == game_handlers.BeatClockFinishClaim.ACQUIRED
    assert second_claim == game_handlers.BeatClockFinishClaim.ALREADY_ACQUIRED
    assert manager._beat_clock_finish_key(session_code) in fake_redis.values


def test_beat_clock_finish_claim_reports_redis_unavailable():
    class FailingRedis(_FakeRedis):
        def set(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

    fake_redis = FailingRedis()

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        claim = manager.claim_beat_clock_finish("SESSION123")

    assert claim == game_handlers.BeatClockFinishClaim.UNAVAILABLE


def test_clear_beat_clock_state_preserves_finish_marker_until_ttl():
    fake_redis = _FakeRedis()
    session_code = "SESSION123"

    with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
        manager.claim_beat_clock_finish(session_code)
        finish_key = manager._beat_clock_finish_key(session_code)

        manager.clear_beat_clock_state(session_code)

    assert finish_key in fake_redis.values
    assert all(finish_key not in keys for keys in fake_redis.delete_calls)


def test_beat_clock_finish_now_uses_db_election_when_redis_marker_exists():
    handler = game_handlers.BeatTheClockGameHandler("SESSION123")
    db = MagicMock()

    async def run_test():
        with patch.object(
            manager,
            "claim_beat_clock_finish",
            side_effect=[
                game_handlers.BeatClockFinishClaim.ACQUIRED,
                game_handlers.BeatClockFinishClaim.ALREADY_ACQUIRED,
            ],
        ):
            with patch.object(
                manager,
                "get_beat_clock_state",
                return_value={"active": True, "players": {}, "leaderboard": []},
            ):
                with patch.object(
                    game_handlers,
                    "handle_game_end",
                    AsyncMock(return_value=True),
                ) as game_end:
                    await handler._finish_now(db, acting_player_id="P1")
                    await handler._finish_now(db, acting_player_id="P1")

        assert game_end.await_count == 2

    asyncio.run(run_test())


def test_beat_clock_finish_now_retries_unavailable_finish_claim_then_finalizes():
    handler = game_handlers.BeatTheClockGameHandler("SESSION123")
    db = MagicMock()

    async def run_test():
        with patch.object(
            manager,
            "claim_beat_clock_finish",
            side_effect=[
                game_handlers.BeatClockFinishClaim.UNAVAILABLE,
                game_handlers.BeatClockFinishClaim.ACQUIRED,
            ],
        ):
            with patch.object(
                manager,
                "get_beat_clock_state",
                return_value={"active": True, "players": {}, "leaderboard": []},
            ):
                with patch.object(
                    game_handlers,
                    "handle_game_end",
                    AsyncMock(return_value=True),
                ) as game_end:
                    await handler._finish_now(db, acting_player_id="P1")

        game_end.assert_awaited_once()

    asyncio.run(run_test())


def test_connection_generation_rejects_stale_mobile_socket():
    fake_redis = _FakeRedis()
    websocket = MagicMock()
    session_code = "SESSION123"
    player_id = "P1"
    ws_id = "ws_old"
    old_generation = "worker-a:ws_old"
    fake_redis.values[manager._player_generation_key(session_code, player_id)] = (
        "worker-b:ws_new"
    )

    manager.active_connections[session_code] = {
        ws_id: {
            "websocket": websocket,
            "client_type": "mobile",
            "player_id": player_id,
            "connection_generation": old_generation,
        }
    }
    manager.websocket_registry[ws_id] = {
        "session_code": session_code,
        "websocket": websocket,
    }

    try:
        with patch.object(redis_bus.websocket_bus, "_sync_redis", fake_redis):
            assert (
                manager.connection_is_current(websocket, session_code, player_id)
                is False
            )
    finally:
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop(ws_id, None)


def test_async_connection_generation_rejects_stale_mobile_socket():
    fake_redis = _FakeAsyncRedis()
    websocket = MagicMock()
    session_code = "SESSION123"
    player_id = "P1"
    ws_id = "ws_old"
    old_generation = "worker-a:ws_old"
    generation_key = manager._player_generation_key(session_code, player_id)
    fake_redis.values[generation_key] = "worker-b:ws_new"

    manager.active_connections[session_code] = {
        ws_id: {
            "websocket": websocket,
            "client_type": "mobile",
            "player_id": player_id,
            "connection_generation": old_generation,
        }
    }
    manager.websocket_registry[ws_id] = {
        "session_code": session_code,
        "websocket": websocket,
    }

    try:
        with patch.object(redis_bus.websocket_bus, "_redis", fake_redis):
            assert (
                asyncio.run(
                    manager.connection_is_current_async(
                        websocket,
                        session_code,
                        player_id,
                    )
                )
                is False
            )
    finally:
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop(ws_id, None)
        manager.websocket_to_ws_id.pop(id(websocket), None)

    assert fake_redis.get_calls == [generation_key]


def test_async_connection_generation_rejects_missing_redis_lease():
    fake_redis = _FakeAsyncRedis()
    websocket = MagicMock()
    session_code = "SESSION123"
    player_id = "P1"
    ws_id = "ws_missing_lease"
    generation_key = manager._player_generation_key(session_code, player_id)

    manager.active_connections[session_code] = {
        ws_id: {
            "websocket": websocket,
            "client_type": "mobile",
            "player_id": player_id,
            "connection_generation": "worker-a:ws_missing_lease",
        }
    }
    manager.websocket_registry[ws_id] = {
        "session_code": session_code,
        "websocket": websocket,
    }

    try:
        with patch.object(redis_bus.websocket_bus, "_redis", fake_redis):
            assert (
                asyncio.run(
                    manager.connection_is_current_async(
                        websocket,
                        session_code,
                        player_id,
                    )
                )
                is False
            )
    finally:
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop(ws_id, None)
        manager.websocket_to_ws_id.pop(id(websocket), None)

    assert fake_redis.get_calls == [generation_key]


def test_async_connection_generation_treats_redis_read_failure_as_available():
    class FailingAsyncRedis(_FakeAsyncRedis):
        async def get(self, key):
            self.get_calls.append(key)
            raise RuntimeError("redis unavailable")

    fake_redis = FailingAsyncRedis()
    websocket = MagicMock()
    session_code = "SESSION123"
    player_id = "P1"
    ws_id = "ws_current"
    generation_key = manager._player_generation_key(session_code, player_id)

    manager.active_connections[session_code] = {
        ws_id: {
            "websocket": websocket,
            "client_type": "mobile",
            "player_id": player_id,
            "connection_generation": "worker-a:ws_current",
        }
    }
    manager.websocket_registry[ws_id] = {
        "session_code": session_code,
        "websocket": websocket,
    }

    try:
        with patch.object(redis_bus.websocket_bus, "_redis", fake_redis):
            assert (
                asyncio.run(
                    manager.connection_is_current_async(
                        websocket,
                        session_code,
                        player_id,
                    )
                )
                is True
            )
    finally:
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop(ws_id, None)
        manager.websocket_to_ws_id.pop(id(websocket), None)

    assert fake_redis.get_calls == [generation_key]


def test_async_connection_generation_reports_unknown_on_redis_failure():
    class FailingAsyncRedis(_FakeAsyncRedis):
        async def get(self, key):
            self.get_calls.append(key)
            raise RuntimeError("redis unavailable")

    fake_redis = FailingAsyncRedis()
    websocket = MagicMock()
    session_code = "SESSION123"
    player_id = "P1"
    ws_id = "ws_unknown"

    manager.active_connections[session_code] = {
        ws_id: {
            "websocket": websocket,
            "client_type": "mobile",
            "player_id": player_id,
            "connection_generation": "worker-a:ws_unknown",
        }
    }
    manager.websocket_registry[ws_id] = {
        "session_code": session_code,
        "websocket": websocket,
    }

    try:
        with patch.object(redis_bus.websocket_bus, "_redis", fake_redis):
            status = asyncio.run(
                manager.connection_generation_status_async(
                    websocket,
                    session_code,
                    player_id,
                )
            )
    finally:
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop(ws_id, None)
        manager.websocket_to_ws_id.pop(id(websocket), None)

    assert status == "unknown"


def test_focus_return_unknown_authority_requires_local_pending_socket():
    websocket = MagicMock()
    other_websocket = MagicMock()
    session_code = "SESSION123"
    player_id = "P1"
    ws_id = "ws_focus"
    manager.pending_focus_losses[session_code] = {
        player_id: {
            "session_code": session_code,
            "player_id": player_id,
            "question_id": "Q1",
            "reason": "left_question_screen",
            "lost_at": datetime.now(UTC).isoformat(),
        }
    }
    connection_info = {
        "websocket": websocket,
        "client_type": "mobile",
        "player_id": player_id,
        "connection_generation": "worker-a:ws_focus",
    }
    manager.active_connections[session_code] = {ws_id: connection_info}
    manager.websocket_registry[ws_id] = {
        "session_code": session_code,
        "websocket": websocket,
    }
    manager.websocket_to_ws_id[id(websocket)] = ws_id
    manager.player_connection_index[(session_code, player_id)] = {ws_id}

    try:
        assert (
            routes.websocket_focus_return_allowed_during_unknown_authority(
                websocket,
                session_code,
                player_id,
            )
            is True
        )
        assert (
            routes.websocket_focus_return_allowed_during_unknown_authority(
                other_websocket,
                session_code,
                player_id,
            )
            is False
        )
    finally:
        manager.pending_focus_losses.pop(session_code, None)
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop(ws_id, None)
        manager.websocket_to_ws_id.pop(id(websocket), None)
        manager.player_connection_index.pop((session_code, player_id), None)


def test_async_connection_generation_claim_uses_async_redis_eval():
    fake_redis = _FakeAsyncRedis()
    session_code = "SESSION123"
    player_id = "P1"
    generation_key = manager._player_generation_key(session_code, player_id)
    fake_redis.values[generation_key] = "old-worker:ws1"

    with patch.object(redis_bus.websocket_bus, "_redis", fake_redis):
        old_generation = asyncio.run(
            manager._claim_player_connection_generation_async(
                session_code,
                player_id,
                "new-worker:ws2",
            )
        )

    assert old_generation == "old-worker:ws1"
    assert fake_redis.values[generation_key] == "new-worker:ws2"
    assert len(fake_redis.eval_calls) == 1


def test_async_connection_generation_renew_extends_owned_lease():
    fake_redis = _FakeAsyncRedis()
    session_code = "SESSION123"
    player_id = "P1"
    generation = "worker-a:ws1"
    generation_key = manager._player_generation_key(session_code, player_id)
    fake_redis.values[generation_key] = generation

    with patch.object(redis_bus.websocket_bus, "_redis", fake_redis):
        renewed = asyncio.run(
            manager._renew_player_connection_generation_async(
                session_code,
                player_id,
                generation,
            )
        )

    assert renewed is True
    assert fake_redis.values[generation_key] == generation
    assert fake_redis.expire_calls == [
        (generation_key, str(manager.PRESENCE_KEY_TTL_SECONDS))
    ]


def test_connection_indexes_track_websocket_and_player_connections():
    websocket = MagicMock()
    session_code = "SESSION789"
    player_id = "P1"
    ws_id = "ws_indexed"
    connection_info = {
        "websocket": websocket,
        "client_type": "mobile",
        "player_id": player_id,
        "player_name": "Alice",
    }

    manager.active_connections[session_code] = {ws_id: connection_info}
    manager.websocket_registry[ws_id] = {
        "session_code": session_code,
        "websocket": websocket,
    }

    try:
        manager._register_connection_indexes(session_code, ws_id, connection_info)

        assert manager._connection_info_for_websocket(websocket) is connection_info
        assert manager.get_player_connections(session_code, player_id) == {
            ws_id: connection_info
        }

        manager._remove_connection_indexes(session_code, ws_id, connection_info)

        assert id(websocket) not in manager.websocket_to_ws_id
        assert (session_code, player_id) not in manager.player_connection_index
    finally:
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop(ws_id, None)
        manager.websocket_to_ws_id.pop(id(websocket), None)
        manager.player_connection_index.pop((session_code, player_id), None)


def test_revoke_connection_generation_closes_only_matching_socket():
    stale_socket = MagicMock()
    stale_socket.close = AsyncMock()
    current_socket = MagicMock()
    current_socket.close = AsyncMock()
    session_code = "SESSION456"
    player_id = "P1"

    manager.active_connections[session_code] = {
        "ws_stale": {
            "websocket": stale_socket,
            "client_type": "mobile",
            "player_id": player_id,
            "connection_generation": "worker-a:ws_stale",
        },
        "ws_current": {
            "websocket": current_socket,
            "client_type": "mobile",
            "player_id": player_id,
            "connection_generation": "worker-b:ws_current",
        },
    }
    manager.websocket_registry["ws_stale"] = {
        "session_code": session_code,
        "websocket": stale_socket,
    }
    manager.websocket_registry["ws_current"] = {
        "session_code": session_code,
        "websocket": current_socket,
    }

    try:
        disconnected = asyncio.run(
            manager._disconnect_local_generation(
                session_code,
                player_id,
                "worker-a:ws_stale",
                close_code=4000,
                reason="New connection established",
            )
        )
    finally:
        manager.active_connections.pop(session_code, None)
        manager.websocket_registry.pop("ws_stale", None)
        manager.websocket_registry.pop("ws_current", None)

    assert disconnected == 1
    stale_socket.close.assert_awaited_once_with(
        code=4000,
        reason="New connection established",
    )
    current_socket.close.assert_not_awaited()


def test_buzzer_ui_update_sends_answer_data_only_to_winner():
    winner_ws = MagicMock()
    waiting_ws = MagicMock()
    question = {
        "question_id": "Q1",
        "question": "Pick one",
        "genre": "Trivia",
        "difficulty": "easy",
        "display_options": ["A", "B", "C", "D"],
    }

    handler = game_handlers.BuzzerGameHandler("SESSION123")

    with patch.object(game_handlers, "manager") as mock_manager:
        mock_manager.get_buzzer_state.return_value = {
            "current_buzzer_winner": "P1",
            "frozen_players": set(),
            "question_active": True,
            "current_question_id": "Q1",
            "attempts": [],
            "accepting_buzzes": True,
        }
        mock_manager.get_session_connections.return_value = {
            "ws1": {
                "client_type": "mobile",
                "player_id": "P1",
                "websocket": winner_ws,
            },
            "ws2": {
                "client_type": "mobile",
                "player_id": "P2",
                "websocket": waiting_ws,
            },
        }
        mock_manager.send_personal_message = AsyncMock()

        with patch.object(
            game_handlers,
            "get_current_question_details",
            return_value={"current_question": question},
        ):
            with patch.object(
                game_handlers,
                "get_player_by_ID",
                return_value=SimpleNamespace(player_name="Winner"),
            ):
                asyncio.run(handler.update_mobile_buzzer_ui(MagicMock()))

    sent_messages = [
        call.args[0]["data"]
        for call in mock_manager.send_personal_message.await_args_list
    ]
    winner_message = next(
        data for data in sent_messages if data["button_state"] == "answer_mode"
    )
    waiting_message = next(
        data for data in sent_messages if data["button_state"] == "waiting"
    )

    assert winner_message["is_current_player"] is True
    assert winner_message["ui_mode"] == "multiple_choice"
    assert winner_message["question_id"] == "Q1"
    assert winner_message["display_options"] == ["A", "B", "C", "D"]
    assert waiting_message["is_current_player"] is False
    assert waiting_message["current_buzzer_winner"] == "P1"
