from pydantic import BaseModel, Field


class PasswordResetRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20)


class PasswordVerifyRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20)
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class PasswordUpdateRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20)
    reset_token: str = Field(..., min_length=20)
    new_password: str = Field(..., min_length=8, max_length=128)
