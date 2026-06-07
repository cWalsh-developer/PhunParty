import re


def normalize_phone_number(number: str | None) -> str | None:
    if not number:
        return None

    cleaned = re.sub(r"[\s().-]+", "", number.strip())
    if not cleaned:
        return None

    if cleaned.startswith("0"):
        return "+44" + cleaned[1:]

    if cleaned.startswith("+"):
        return cleaned

    raise ValueError("Invalid phone number format")


def phone_number_candidates(number: str | None) -> list[str]:
    if not number:
        return []

    candidates = [number.strip()]
    try:
        normalized = normalize_phone_number(number)
    except ValueError:
        normalized = None

    if normalized:
        candidates.append(normalized)
        if normalized.startswith("+44"):
            candidates.append("0" + normalized[3:])

    deduped = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped
