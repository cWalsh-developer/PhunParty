from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.websockets.manager import SessionPhase, manager
from app.websockets.scheduler import utc_now


def _parse_phase_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def is_question_accepting_answers(
    session_code: str,
    question_id: str | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    phase_state = manager.get_session_phase_state(session_code)
    if phase_state.get("phase") != SessionPhase.QUESTION.value:
        return False, "question_not_active"

    current_question_id = phase_state.get("current_question_id")
    if not question_id or question_id != current_question_id:
        return False, "stale_question"

    now = (now or utc_now()).replace(tzinfo=None)
    start_at = _parse_phase_time(phase_state.get("start_at"))
    if start_at and now < start_at:
        return False, "question_not_started"

    expires_at = _parse_phase_time(
        phase_state.get("question_expires_at") or phase_state.get("expires_at")
    )
    if expires_at and now >= expires_at:
        return False, "question_expired"

    return True, "ok"


def assert_question_accepting_answers(
    session_code: str, question_id: str | None
) -> None:
    allowed, reason = is_question_accepting_answers(session_code, question_id)
    if not allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": reason,
                "message": "This question is not currently accepting answers.",
            },
        )


def should_expose_current_question(session_code: str, question_id: str | None) -> bool:
    allowed, _reason = is_question_accepting_answers(session_code, question_id)
    return allowed
