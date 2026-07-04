import hashlib
import logging
import os
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from app.security.input_validation import normalize_email
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parents[2] / "credentials.env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

CODE_TTL_MINUTES = 15


def generate_email_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def email_verification_expires_at() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(
        minutes=CODE_TTL_MINUTES
    )


def hash_email_verification_code(email: str, code: str) -> str:
    secret = os.getenv("EMAIL_VERIFICATION_SECRET") or os.getenv(
        "SECRET_KEY",
        "phunparty-email-verification-dev-secret",
    )
    normalized_email = normalize_email(email)
    payload = f"{normalized_email}:{code.strip()}:{secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def send_email_verification_code(to_email: str, code: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM_EMAIL") or smtp_username
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "PhunParty")
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false"

    if not smtp_host or not smtp_from:
        logger.warning(
            "Email verification SMTP is not configured. Verification code for %s is %s",
            to_email,
            code,
        )
        return False

    message = EmailMessage()
    message["Subject"] = "Your PhunParty verification code"
    message["From"] = f"{smtp_from_name} <{smtp_from}>"
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                "Welcome to PhunParty!",
                "",
                f"Your verification code is: {code}",
                "",
                f"This code expires in {CODE_TTL_MINUTES} minutes.",
                "",
                "If you did not create a PhunParty account, you can ignore this email.",
            ]
        )
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)

    logger.info("Email verification code sent to %s", to_email)
    return True
