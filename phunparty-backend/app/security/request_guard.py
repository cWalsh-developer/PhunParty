import json
import os
import re
from typing import Any
from urllib.parse import unquote_plus

from app.security.input_validation import sanitize_input
from app.security.rate_limit import get_client_ip, rate_limiter, stable_hash
from app.utils.generateJWT import ALGORITHM, SECRET_KEY
from fastapi import Request
from jose import JWTError, jwt
from starlette.responses import JSONResponse

JSON_CONTENT_TYPES = {
    "application/json",
    "application/merge-patch+json",
}

SCRIPT_PATTERN = re.compile(
    r"(<\s*script\b|javascript\s*:|data\s*:\s*text/html|" r"\bon[a-z]+\s*=)",
    re.IGNORECASE,
)
PATH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,128}$")


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


MAX_JSON_BODY_BYTES = int_env("MAX_JSON_BODY_BYTES", 512 * 1024)
ENDPOINT_RATE_LIMIT = int_env("ENDPOINT_RATE_LIMIT", 120)
ENDPOINT_RATE_WINDOW_SECONDS = int_env("ENDPOINT_RATE_WINDOW_SECONDS", 60)
ENDPOINT_BURST_LIMIT = int_env("ENDPOINT_BURST_LIMIT", 30)
ENDPOINT_BURST_WINDOW_SECONDS = int_env("ENDPOINT_BURST_WINDOW_SECONDS", 10)

GUARD_EXCLUDED_PATHS = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def json_error(status_code: int, detail: str, headers: dict[str, str] | None = None):
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers,
    )


def is_json_request(request: Request) -> bool:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    return content_type in JSON_CONTENT_TYPES or content_type.endswith("+json")


def decode_subject_from_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or not SECRET_KEY:
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

    subject = payload.get("sub")
    return subject if isinstance(subject, str) and subject else None


def endpoint_key(request: Request) -> str:
    segments = []
    for segment in request.url.path.strip("/").split("/"):
        decoded = unquote_plus(segment)
        if PATH_TOKEN_PATTERN.fullmatch(decoded):
            segments.append("{id}")
        else:
            segments.append(decoded.lower())

    normalized_path = "/" + "/".join(segments) if segments else "/"
    return f"{request.method.upper()}:{normalized_path}"


def contains_suspicious_string(value: str) -> bool:
    return bool(SCRIPT_PATTERN.search(unquote_plus(value)))


def contains_suspicious_value(value: Any) -> bool:
    if isinstance(value, str):
        return contains_suspicious_string(value)
    if isinstance(value, list):
        return any(contains_suspicious_value(item) for item in value)
    if isinstance(value, dict):
        return any(
            contains_suspicious_string(str(key)) or contains_suspicious_value(item)
            for key, item in value.items()
        )
    return False


def validate_path_and_query(request: Request):
    if contains_suspicious_string(request.url.path):
        return json_error(400, "Invalid request path")

    for key, value in request.query_params.multi_items():
        if contains_suspicious_string(key) or contains_suspicious_string(value):
            return json_error(400, "Invalid query parameter")

    return None


async def read_and_validate_json_body(request: Request) -> JSONResponse | None:
    if not is_json_request(request):
        return None

    body = await request.body()
    if not body:
        return None

    if len(body) > MAX_JSON_BODY_BYTES:
        return json_error(413, "JSON request body is too large")

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return json_error(400, "Malformed JSON request body")

    if not isinstance(parsed, (dict, list)):
        return json_error(422, "JSON request body must be an object or array")

    sanitized = sanitize_input(parsed)
    sanitized_body = json.dumps(sanitized, separators=(",", ":")).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": sanitized_body, "more_body": False}

    request._receive = receive
    return None


async def enforce_endpoint_pattern_limits(request: Request) -> JSONResponse | None:
    if request.url.path in GUARD_EXCLUDED_PATHS:
        return None

    subject = decode_subject_from_bearer_token(request)
    identity = f"user:{subject}" if subject else f"ip:{get_client_ip(request)}"
    endpoint = endpoint_key(request)
    hashed_identity = stable_hash(f"{identity}:{endpoint}")

    checks = [
        (
            f"rl:endpoint:{hashed_identity}",
            ENDPOINT_RATE_LIMIT,
            ENDPOINT_RATE_WINDOW_SECONDS,
        ),
        (
            f"rl:endpoint-burst:{hashed_identity}",
            ENDPOINT_BURST_LIMIT,
            ENDPOINT_BURST_WINDOW_SECONDS,
        ),
    ]

    for key, limit, window_seconds in checks:
        allowed, retry_after = await rate_limiter.hit(key, limit, window_seconds)
        if not allowed:
            return json_error(
                429,
                "Too many requests to this endpoint. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    return None
