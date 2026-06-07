import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_player_by_phone, store_otp
from app.database.dbCRUD import update_password as updatePassword
from app.database.dbCRUD import verify_otp
from app.dependencies import decode_access_token, get_db
from app.models.passwordResetModel import (
    PasswordResetRequest,
    PasswordUpdateRequest,
    PasswordVerifyRequest,
)
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from app.security.rls import set_rls_current_player, set_rls_reset_phone
from app.utils.generateJWT import create_access_token
from app.utils.sendSMS import format_number_uk, send_sms

router = APIRouter()

GENERIC_RESET_MESSAGE = "If that phone number is registered, a reset code will be sent."


def generate_otp():
    """Generate a 6-digit OTP"""
    return str(random.randint(100000, 999999))


def create_password_reset_token(player_id: str, phone_number: str) -> str:
    return create_access_token(
        data={
            "sub": player_id,
            "phone": phone_number,
            "purpose": "password_reset",
        },
        expires_delta=timedelta(minutes=10),
    )


def verify_password_reset_token(token: str, phone_number: str) -> str:
    payload = decode_access_token(token)

    if (
        payload.get("purpose") != "password_reset"
        or payload.get("phone") != phone_number
        or not payload.get("sub")
    ):
        raise HTTPException(status_code=401, detail="Invalid reset token")

    return payload["sub"]


def get_phone_candidates(phone_number: str) -> list[str]:
    candidates = [phone_number.strip()]
    try:
        formatted = format_number_uk(phone_number)
        candidates.append(formatted)
        if formatted.startswith("+44"):
            candidates.append("0" + formatted[3:])
    except ValueError:
        pass

    deduped = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def find_player_for_reset(db: Session, phone_number: str):
    for candidate in get_phone_candidates(phone_number):
        set_rls_reset_phone(db, candidate)
        player = get_player_by_phone(db, candidate)
        if player:
            return player, candidate

    return None, None


@router.post("/request", tags=["Password Reset"])
async def request_password_reset(
    request: Request,
    phone: PasswordResetRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="password-reset-ip",
        identifier=get_client_ip(request),
        limit=5,
        window_seconds=3600,
    )
    await enforce_rate_limit(
        request,
        scope="password-reset-phone",
        identifier=phone.phone_number,
        limit=3,
        window_seconds=3600,
    )

    try:
        player, stored_phone = find_player_for_reset(db, phone.phone_number)
        if not player:
            return {"message": GENERIC_RESET_MESSAGE}

        otp = generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        set_rls_reset_phone(db, stored_phone)
        record = store_otp(db, stored_phone, otp, expires_at)
        if not record:
            raise HTTPException(status_code=500, detail="Failed to store OTP")

        message = f"Your password reset code is: {otp}"
        number = format_number_uk(stored_phone)
        result = send_sms(number, message, db)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to send SMS")

        return {"message": GENERIC_RESET_MESSAGE}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Password reset service temporarily unavailable"
        )


@router.post("/verify", tags=["Password Reset"])
async def verify_otp_route(
    request: Request,
    phone: PasswordVerifyRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="password-verify-ip",
        identifier=get_client_ip(request),
        limit=20,
        window_seconds=3600,
    )
    await enforce_rate_limit(
        request,
        scope="password-verify-phone",
        identifier=phone.phone_number,
        limit=5,
        window_seconds=900,
    )

    try:
        player, stored_phone = find_player_for_reset(db, phone.phone_number)
        if not player:
            raise HTTPException(
                status_code=400, detail="Invalid or expired verification code"
            )

        set_rls_reset_phone(db, stored_phone)
        is_valid = verify_otp(db, stored_phone, phone.otp)
        if not is_valid:
            raise HTTPException(
                status_code=400, detail="Invalid or expired verification code"
            )

        return {
            "message": "Verification code confirmed",
            "reset_token": create_password_reset_token(
                player.player_id,
                stored_phone,
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Verification service temporarily unavailable"
        )


@router.put("/update", tags=["Password Reset"])
async def update_password(
    request: Request,
    phone: PasswordUpdateRequest,
    db: Session = Depends(get_db),
):
    await enforce_rate_limit(
        request,
        scope="password-update-ip",
        identifier=get_client_ip(request),
        limit=10,
        window_seconds=3600,
    )

    try:
        player, stored_phone = find_player_for_reset(db, phone.phone_number)
        if not player:
            raise HTTPException(status_code=401, detail="Invalid reset token")

        player_id = verify_password_reset_token(phone.reset_token, stored_phone)
        set_rls_reset_phone(db, stored_phone)
        if not player or player.player_id != player_id:
            raise HTTPException(status_code=401, detail="Invalid reset token")

        set_rls_current_player(db, player.player_id)
        is_updated = updatePassword(db, stored_phone, phone.new_password)
        if not is_updated:
            raise HTTPException(
                status_code=400,
                detail="Failed to update password",
            )
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Password update service temporarily unavailable"
        )
