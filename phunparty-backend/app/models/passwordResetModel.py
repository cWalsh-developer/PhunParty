from pydantic import BaseModel


class PasswordResetRequest(BaseModel):
    phone_number: str


class PasswordVerifyRequest(BaseModel):
    phone_number: str
    otp: str


class PasswordUpdateRequest(BaseModel):
    phone_number: str
    new_password: str
