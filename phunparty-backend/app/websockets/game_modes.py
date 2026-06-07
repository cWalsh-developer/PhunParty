"""Helpers for resolving session game modes."""

import json
import logging
from typing import Any, Optional

from app.database.dbCRUD import get_game_by_code, get_session_by_code
from app.websockets.manager import manager
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_GAME_TYPE = "trivia"
BUZZER_GAME_TYPE = "buzzer"


def normalize_game_type(*candidates: Any) -> Optional[str]:
    """Normalize known game-mode values from request, session, or game metadata."""
    for candidate in candidates:
        if candidate is None:
            continue

        values = []
        if isinstance(candidate, dict):
            values.extend(candidate.values())
        else:
            values.append(candidate)

        for value in values:
            if value is None:
                continue

            text = str(value).strip().lower()
            if not text:
                continue

            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None

            if isinstance(parsed, dict):
                parsed_type = normalize_game_type(
                    parsed.get("game_type"),
                    parsed.get("mode"),
                    parsed.get("session_type"),
                    parsed.get("quiz_type"),
                    parsed.get("type"),
                )
                if parsed_type:
                    return parsed_type

            if "buzzer" in text or "buzz" in text:
                return BUZZER_GAME_TYPE
            if "trivia" in text or "quiz" in text:
                return DEFAULT_GAME_TYPE

    return None


def resolve_session_game_type(
    db: Session,
    session_code: str,
    session: Any = None,
    requested_game_type: Optional[str] = None,
) -> str:
    """Resolve a session's game mode, defaulting safely to trivia."""
    requested = normalize_game_type(requested_game_type)
    if requested:
        manager.set_session_game_type(session_code, requested)
        return requested

    session = session or get_session_by_code(db, session_code)
    session_type = normalize_game_type(
        getattr(session, "game_type", None),
        getattr(session, "mode", None),
        getattr(session, "session_type", None),
        getattr(session, "quiz_type", None),
    )
    if session_type:
        manager.set_session_game_type(session_code, session_type)
        return session_type

    game_code = getattr(session, "game_code", None)
    if game_code:
        game = get_game_by_code(db, game_code)
        game_type = normalize_game_type(
            getattr(game, "game_type", None),
            getattr(game, "mode", None),
            getattr(game, "rules", None),
            getattr(game, "genre", None),
        )
        if game_type:
            manager.set_session_game_type(session_code, game_type)
            return game_type

    stored = normalize_game_type(manager.get_session_game_type(session_code))
    if stored:
        return stored

    logger.info(f"Defaulting session {session_code} to {DEFAULT_GAME_TYPE} mode")
    manager.set_session_game_type(session_code, DEFAULT_GAME_TYPE)
    return DEFAULT_GAME_TYPE
