from typing import Any

SENSITIVE_QUESTION_FIELDS = {
    "answer",
    "correct_answer",
    "correct_index",
}


def sanitize_question_for_client(value: Any) -> Any:
    """Remove server-only answer fields from outbound question payloads."""
    if isinstance(value, list):
        return [sanitize_question_for_client(item) for item in value]

    if not isinstance(value, dict):
        return value

    return {
        key: sanitize_question_for_client(item)
        for key, item in value.items()
        if key not in SENSITIVE_QUESTION_FIELDS
    }
