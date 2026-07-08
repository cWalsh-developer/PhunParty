from app.security.input_validation import SanitizedRequestModel
from pydantic import Field


class PasswordResetRequest(SanitizedRequestModel):
    phone_number: str = Field(..., min_length=7, max_length=20)


class PasswordVerifyRequest(SanitizedRequestModel):
    phone_number: str = Field(..., min_length=7, max_length=20)
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class PasswordUpdateRequest(SanitizedRequestModel):
    phone_number: str = Field(..., min_length=7, max_length=20)
    reset_token: str = Field(..., min_length=20)
    new_password: str = Field(..., min_length=8, max_length=128)
