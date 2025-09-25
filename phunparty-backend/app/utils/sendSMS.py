import os

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from twilio.rest import Client

from app.database.dbCRUD import delete_expired_otps
from app.dependencies import get_db

load_dotenv()
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")


def format_number_uk(number: str) -> str:
    if number.startswith("0"):
        return "+44" + number[1:]
    elif number.startswith("+"):
        return number  # already E.164
    else:
        raise ValueError("Invalid phone number format")


def send_sms(to_number: str, message: str, db: Session) -> bool:
    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)

        response = client.messages.create(
            body=message, from_=TWILIO_PHONE, to=to_number
        )

        delete_expired_otps(db)
        return True
    except Exception as e:
        print("❌ SMS Error:", str(e))
        raise HTTPException(status_code=500, detail="Failed to send SMS: " + str(e))
