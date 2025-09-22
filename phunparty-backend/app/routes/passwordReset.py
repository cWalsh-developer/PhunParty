import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_player_by_phone, store_otp
from app.database.dbCRUD import update_password as updatePassword
from app.database.dbCRUD import verify_otp
from app.dependencies import get_api_key, get_db
from app.models.passwordResetModel import (PasswordResetRequest,
                                           PasswordUpdateRequest,
                                           PasswordVerifyRequest)
from app.utils.generateJWT import create_access_token
from app.utils.sendSMS import format_number_uk, send_sms

router = APIRouter(dependencies=[Depends(get_api_key)])


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


@router.put("/update", tags=["Password Reset"])
def update_password(phone: PasswordUpdateRequest, db: Session = Depends(get_db)):
    is_updated = updatePassword(db, phone.phone_number, phone.new_password)
    if not is_updated:
        raise HTTPException(
            status_code=400,
            detail="Failed to update password. Please try again.",
        )
    player = get_player_by_phone(db, phone.phone_number)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    access_token = create_access_token(
        data={
            "sub": player.player_id,
        }
    )
    return {
        "message": "Password updated successfully",
        "access_token": access_token,
        "token_type": "bearer",
    }
