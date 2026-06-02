import asyncio
import os
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy

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
from app.logic import answer_validation
from app.logic import game_logic
from app.schemas.game_state_models import GameSessionState
from app.websockets import game_handlers, game_lifecycle, game_modes, routes, scheduler
from app.websockets.manager import SessionPhase, manager

sqlalchemy.create_engine = _real_create_engine


def test_game_session_state_model_restores_timestamp_columns():
    assert hasattr(GameSessionState, "started_at")
    assert hasattr(GameSessionState, "ended_at")


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
            with patch.object(dbCRUD, "get_game_session_state", return_value=game_state):
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
    mock_db.commit.assert_called_once()


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
    mock_db = MagicMock()

    with patch.object(game_logic, "get_player_response", return_value=None):
        with patch.object(game_logic, "get_question_by_id", return_value=question):
            with patch.object(game_logic, "create_player_response"):
                with patch.object(game_logic, "update_scores"):
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
    assert result["answer_match"]["matched_answer"] == "Cameron"


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
        started_at=datetime.utcnow() - timedelta(seconds=60),
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
    mock_manager.broadcast_to_session.assert_awaited_once()


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


def test_game_lifecycle_broadcasts_final_scores_for_already_ended_session():
    ended_at = datetime(2026, 6, 1, 12, 0, 0)
    game_state = SimpleNamespace(ended_at=ended_at)
    final_scores = [{"player_id": "P1", "player_name": "Player", "score": 2}]
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = game_state

    with patch.object(
        game_lifecycle, "update_game_session_ended", return_value=False
    ):
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

    assert result is True
    mock_manager.clear_question_queue.assert_called_once_with("SESSION123")
    mock_manager.broadcast_to_session.assert_awaited_once()
    message = mock_manager.broadcast_to_session.await_args.args[1]
    assert message["type"] == "game_ended"
    assert message["data"]["final_scores"] == final_scores


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
    assert state["current_question_id"] == "Q2"
    assert state["current_buzzer_winner"] is None
    assert state["frozen_players"] == set()


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
    winner_message = next(data for data in sent_messages if data["button_state"] == "answer_mode")
    waiting_message = next(data for data in sent_messages if data["button_state"] == "waiting")

    assert winner_message["is_current_player"] is True
    assert winner_message["ui_mode"] == "multiple_choice"
    assert winner_message["question_id"] == "Q1"
    assert winner_message["display_options"] == ["A", "B", "C", "D"]
    assert waiting_message["is_current_player"] is False
    assert waiting_message["current_buzzer_winner"] == "P1"
