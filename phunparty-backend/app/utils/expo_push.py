import logging
from hashlib import sha256
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def token_ref(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()[:12]


async def send_expo_push(
    token: str, title: str, body: str, data: Optional[dict] = None
) -> bool:
    payload = {
        "to": token,
        "sound": "default",
        "title": title,
        "body": body,
        "data": data or {},
    }

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as client:
            async with client.post(EXPO_PUSH_URL, json=payload) as response:
                response_text = await response.text()
                if response.status >= 400:
                    logger.warning(
                        "Expo push failed token_ref=%s status=%s",
                        token_ref(token),
                        response.status,
                    )
                    return False
                logger.debug("Expo push response status=%s", response.status)
                return (
                    '"status":"ok"' in response_text
                    or '"status": "ok"' in response_text
                )
    except Exception:
        logger.exception("Expo push failed token_ref=%s", token_ref(token))
        return False


async def send_expo_push_to_tokens(
    tokens: list[str], title: str, body: str, data: Optional[dict] = None
) -> None:
    for token in tokens:
        success = await send_expo_push(token, title, body, data)
        logger.warning("Expo push send completed successfully=%s", success)
