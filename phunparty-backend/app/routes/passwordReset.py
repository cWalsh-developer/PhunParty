from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timedelta, timezone
import random
from app.database.dbCRUD import store_otp, verify_otp, delete_expired_otps
from app.dependencies import get_db
from sqlalchemy.orm import Session
from app.utils.sendSMS import send_sms, format_number_uk
from app.models.passwordResetModel import PasswordResetRequest, PasswordVerifyRequest

router = APIRouter()


def generate_otp():
    """Generate a 6-digit OTP"""
    return str(random.randint(100000, 999999))


import traceback


@router.post("/request", tags=["Password Reset"])
def request_password_reset(phone: PasswordResetRequest, db: Session = Depends(get_db)):
    try:
        otp = generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        record = store_otp(db, phone.phone_number, otp, expires_at)
        if not record:
            raise HTTPException(status_code=500, detail="Failed to store OTP")

        message = f"Your password reset code is: {otp}"
        number = format_number_uk(phone.phone_number)
        result = send_sms(number, message, db)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to send SMS")

        return {"message": "OTP sent via SMS"}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Unhandled exception: {str(e)}")


@router.post("/verify", tags=["Password Reset"])
def verify_otp_route(phone: PasswordVerifyRequest, db: Session = Depends(get_db)):
    is_valid = verify_otp(db, phone.phone_number, phone.otp)
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    return {"message": "OTP verified successfully"}
