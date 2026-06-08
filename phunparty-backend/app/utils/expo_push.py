import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


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
                if response.status >= 400:
                    response_text = await response.text()
                    logger.warning(
                        "Expo push failed for token %s: %s %s",
                        token,
                        response.status,
                        response_text,
                    )
                    return False
                logger.warning(
                    "Expo push response: status=%s body=%s",
                    response.status,
                    response_text,
                )
                return (
                    '"status":"ok"' in response_text
                    or '"status": "ok"' in response_text
                )
    except Exception:
        logger.exception("Expo push failed for token %s", token)
        return False


async def send_expo_push_to_tokens(
    tokens: list[str], title: str, body: str, data: Optional[dict] = None
) -> None:
    for token in tokens:
        success = await send_expo_push(token, title, body, data)
        logger.warning("Expo push send completed successfully=%s", success)
