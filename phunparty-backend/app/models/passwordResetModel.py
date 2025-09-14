from pydantic import BaseModel


class PasswordResetRequest(BaseModel):
    phone_number: str


class PasswordVerifyRequest(BaseModel):
    phone_number: str
    otp: str
