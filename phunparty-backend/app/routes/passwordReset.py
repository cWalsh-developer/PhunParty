import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from app.config import SessionLocal
from app.database.refresh_token_crud import (
    create_refresh_session,
    revoke_all_player_refresh_tokens,
)
from app.database.dbCRUD import (
    consume_password_reset_jti,
    get_player_by_phone,
    store_otp,
    store_password_reset_jti,
)
from app.database.dbCRUD import update_password as updatePassword
from app.database.dbCRUD import verify_otp
from app.dependencies import get_db
from app.models.passwordResetModel import (
    PasswordResetRequest,
    PasswordUpdateRequest,
    PasswordVerifyRequest,
)
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from app.security.rls import set_rls_current_player, set_rls_reset_phone
from app.utils.generateJWT import (
    ALGORITHM,
    PASSWORD_RESET_AUDIENCE,
    PASSWORD_RESET_TOKEN_TYPE,
    SECRET_KEY,
    create_access_token,
    create_password_reset_token as create_password_reset_jwt,
)
from app.utils.phone_numbers import normalize_phone_number
from app.utils.sendSMS import format_number_uk, send_sms
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

router = APIRouter()
logger = logging.getLogger(__name__)

GENERIC_RESET_MESSAGE = "If that phone number is registered, a reset code will be sent."
RESET_REQUEST_MIN_SECONDS = 0.6


def generate_otp():
    """Generate a 6-digit OTP"""
    return f"{secrets.randbelow(1_000_000):06d}"


def mask_phone_for_log(phone_number: str | None) -> str:
    if not phone_number:
        return "unknown"
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def reset_rate_identifier(phone_number: str) -> str:
    try:
        normalized = normalize_phone_number(phone_number)
    except ValueError:
        normalized = None

    return normalized or phone_number.strip().lower()


async def equalize_reset_request_timing(started_at: float) -> None:
    remaining = RESET_REQUEST_MIN_SECONDS - (time.perf_counter() - started_at)
    if remaining > 0:
        await asyncio.sleep(remaining)


def deliver_password_reset_sms(number: str, message: str, phone_ref: str) -> None:
    db = SessionLocal()
    try:
        if not send_sms(number, message, db):
            logger.warning(
                "Password reset SMS provider did not send to %s",
                phone_ref,
            )
    except Exception:
        logger.exception("Password reset SMS delivery failed for %s", phone_ref)
    finally:
        db.close()


def create_password_reset_token(db: Session, player_id: str, phone_number: str) -> str:
    jti = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    store_password_reset_jti(db, phone_number, jti, expires_at)
    return create_password_reset_jwt(
        data={
            "sub": player_id,
            "phone": phone_number,
            "purpose": "password_reset",
            "jti": jti,
        },
        expires_delta=expires_at - datetime.now(timezone.utc),
    )


def verify_password_reset_token(token: str, phone_number: str) -> tuple[str, str]:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=PASSWORD_RESET_AUDIENCE,
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid reset token")

    if (
        payload.get("token_type") != PASSWORD_RESET_TOKEN_TYPE
        or payload.get("purpose") != "password_reset"
        or payload.get("phone") != phone_number
        or not payload.get("sub")
        or not payload.get("jti")
    ):
        raise HTTPException(status_code=401, detail="Invalid reset token")

    return payload["sub"], payload["jti"]


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
    background_tasks: BackgroundTasks,
    phone: PasswordResetRequest,
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    phone_identifier = reset_rate_identifier(phone.phone_number)
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
        identifier=phone_identifier,
        limit=3,
        window_seconds=3600,
    )

    try:
        player, stored_phone = find_player_for_reset(db, phone.phone_number)
        if not player:
            await equalize_reset_request_timing(started_at)
            return {"message": GENERIC_RESET_MESSAGE}

        otp = generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        set_rls_reset_phone(db, stored_phone)
        record = store_otp(db, stored_phone, otp, expires_at)
        if not record:
            raise HTTPException(status_code=500, detail="Failed to store OTP")

        message = f"Your password reset code is: {otp}"
        number = format_number_uk(stored_phone)
        background_tasks.add_task(
            deliver_password_reset_sms,
            number,
            message,
            mask_phone_for_log(stored_phone),
        )

        await equalize_reset_request_timing(started_at)
        return {"message": GENERIC_RESET_MESSAGE}
    except HTTPException:
        await equalize_reset_request_timing(started_at)
        raise
    except Exception:
        logger.exception("Password reset request failed")
        await equalize_reset_request_timing(started_at)
        raise HTTPException(
            status_code=500, detail="Password reset service temporarily unavailable"
        )


@router.post("/verify", tags=["Password Reset"])
async def verify_otp_route(
    request: Request,
    phone: PasswordVerifyRequest,
    db: Session = Depends(get_db),
):
    phone_identifier = reset_rate_identifier(phone.phone_number)
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
        identifier=phone_identifier,
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
                db,
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

        player_id, reset_jti = verify_password_reset_token(
            phone.reset_token, stored_phone
        )
        set_rls_reset_phone(db, stored_phone)
        if not player or player.player_id != player_id:
            raise HTTPException(status_code=401, detail="Invalid reset token")

        set_rls_current_player(db, player.player_id)
        if not consume_password_reset_jti(db, stored_phone, reset_jti):
            raise HTTPException(status_code=401, detail="Invalid reset token")

        is_updated = updatePassword(db, stored_phone, phone.new_password, commit=False)
        if not is_updated:
            raise HTTPException(
                status_code=400,
                detail="Failed to update password",
            )

        revoke_all_player_refresh_tokens(db, player.player_id, commit=False)
        refresh_token, _refresh_record = create_refresh_session(
            db,
            player.player_id,
            user_agent=request.headers.get("user-agent"),
            ip_address=get_client_ip(request),
            commit=False,
        )
        db.commit()
        access_token = create_access_token(
            data={
                "sub": player.player_id,
            }
        )
        return {
            "message": "Password updated successfully",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Password update service temporarily unavailable"
        )
