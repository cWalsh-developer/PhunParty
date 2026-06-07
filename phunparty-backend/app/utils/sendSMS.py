import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy.orm import Session
from twilio.rest import Client

from app.database.dbCRUD import delete_expired_otps
from app.utils.phone_numbers import normalize_phone_number

logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parents[2] / "credentials.env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()


def format_number_uk(number: str) -> str:
    normalized = normalize_phone_number(number)
    if not normalized:
        raise ValueError("Invalid phone number format")
    return normalized


def get_twilio_config() -> tuple[str, str, str]:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)

    sid = os.getenv("TWILIO_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    phone = os.getenv("TWILIO_PHONE_NUMBER")

    missing = [
        name
        for name, value in {
            "TWILIO_SID": sid,
            "TWILIO_AUTH_TOKEN": token,
            "TWILIO_PHONE_NUMBER": phone,
        }.items()
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"SMS provider is not configured: {', '.join(missing)}",
        )

    return sid, token, format_number_uk(phone)


def send_sms(to_number: str, message: str, db: Session) -> bool:
    try:
        sid, token, from_number = get_twilio_config()
        client = Client(sid, token)

        response = client.messages.create(
            body=message,
            from_=from_number,
            to=to_number,
        )
        logger.info(
            "Password reset SMS queued through Twilio message_sid=%s status=%s",
            getattr(response, "sid", None),
            getattr(response, "status", None),
        )

        delete_expired_otps(db)
        return True
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("SMS send failed")
        raise HTTPException(status_code=500, detail="Failed to send SMS: " + str(e))
