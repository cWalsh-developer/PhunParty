"""Shared answer normalization and fuzzy validation."""

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, List, Optional


@dataclass(frozen=True)
class AnswerValidationResult:
    is_correct: bool
    method: str
    matched_answer: Optional[str] = None
    score: Optional[float] = None


def normalize_answer(value: Any) -> str:
    """Normalize an answer for fair human text matching."""
    if value is None:
        return ""

    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _levenshtein_distance(left: str, right: str) -> int:
    """Compute edit distance with a compact dynamic-programming row."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    if len(left) > len(right):
        left, right = right, left

    previous = list(range(len(left) + 1))
    for row_index, right_char in enumerate(right, 1):
        current = [row_index]
        for column_index, left_char in enumerate(left, 1):
            insert_cost = current[column_index - 1] + 1
            delete_cost = previous[column_index] + 1
            replace_cost = previous[column_index - 1] + (
                0 if left_char == right_char else 1
            )
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current

    return previous[-1]


def _similarity_ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio() * 100


def _coerce_answer_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        return [stripped]
    return [str(value)]


def accepted_answers_for_question(question: Any) -> List[str]:
    """Return canonical answer plus optional aliases if the model has them."""
    answers: List[str] = []
    answers.extend(_coerce_answer_list(getattr(question, "answer", None)))

    for attr_name in ("accepted_answers", "answer_aliases", "aliases"):
        answers.extend(_coerce_answer_list(getattr(question, attr_name, None)))

    deduped = []
    seen = set()
    for answer in answers:
        normalized = normalize_answer(answer)
        if normalized and normalized not in seen:
            deduped.append(answer)
            seen.add(normalized)
    return deduped


def validate_answer(user_answer: Any, accepted_answers: Iterable[Any]) -> AnswerValidationResult:
    """Validate a free-text answer with exact, alias, and fuzzy matching."""
    user = normalize_answer(user_answer)
    if not user:
        return AnswerValidationResult(False, "empty")

    best_result = AnswerValidationResult(False, "no_match")
    for accepted_answer in accepted_answers:
        accepted = normalize_answer(accepted_answer)
        if not accepted:
            continue

        if user == accepted:
            return AnswerValidationResult(True, "exact", str(accepted_answer), 100)

        distance = _levenshtein_distance(user, accepted)
        accepted_length = len(accepted)
        if accepted_length <= 6 and distance <= 1:
            return AnswerValidationResult(
                True, f"levenshtein:{distance}", str(accepted_answer)
            )
        if accepted_length <= 15 and distance <= 2:
            return AnswerValidationResult(
                True, f"levenshtein:{distance}", str(accepted_answer)
            )

        ratio = _similarity_ratio(user, accepted)
        if ratio >= 88:
            return AnswerValidationResult(
                True, f"ratio:{ratio:.1f}", str(accepted_answer), ratio
            )

        if best_result.score is None or ratio > best_result.score:
            best_result = AnswerValidationResult(
                False, f"ratio:{ratio:.1f}", str(accepted_answer), ratio
            )

    return best_result


def validate_answer_against_question(user_answer: Any, question: Any) -> AnswerValidationResult:
    return validate_answer(user_answer, accepted_answers_for_question(question))
