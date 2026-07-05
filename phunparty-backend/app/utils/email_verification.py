import hashlib
import logging
import os
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode

from app.security.input_validation import normalize_email
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parents[2] / "credentials.env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

CODE_TTL_MINUTES = 15
TOKEN_TTL_MINUTES = 60


def generate_email_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_email_verification_token() -> str:
    return secrets.token_urlsafe(48)


def email_verification_expires_at() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(
        minutes=TOKEN_TTL_MINUTES
    )


def hash_email_verification_code(email: str, code: str) -> str:
    secret = os.getenv("EMAIL_VERIFICATION_SECRET") or os.getenv(
        "SECRET_KEY",
        "phunparty-email-verification-dev-secret",
    )
    normalized_email = normalize_email(email)
    payload = f"{normalized_email}:{code.strip()}:{secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_email_verification_token(token: str) -> str:
    secret = os.getenv("EMAIL_VERIFICATION_SECRET") or os.getenv(
        "SECRET_KEY",
        "phunparty-email-verification-dev-secret",
    )
    payload = f"email-verification-token:{token.strip()}:{secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_email_verification_url(token: str) -> str:
    base_url = os.getenv("EMAIL_VERIFICATION_WEB_URL", "https://phun.party/#/verify-email")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({'token': token})}"


def build_app_email_verification_url(token: str) -> str | None:
    base_url = os.getenv("EMAIL_VERIFICATION_APP_URL", "phunpartymobileapp://verify-email")
    if not base_url:
        return None
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({'token': token})}"


def send_email_verification_link(to_email: str, token: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM_EMAIL") or smtp_username
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "PhunParty")
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false"

    if not smtp_host or not smtp_from:
        logger.warning(
            "Email verification SMTP is not configured. Verification link for %s is %s",
            to_email,
            build_email_verification_url(token),
        )
        return False

    verification_url = build_email_verification_url(token)
    app_verification_url = build_app_email_verification_url(token)

    message = EmailMessage()
    message["Subject"] = "Verify your PhunParty email"
    message["From"] = f"{smtp_from_name} <{smtp_from}>"
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                "Welcome to PhunParty!",
                "",
                "Verify your email by opening this link:",
                verification_url,
                "",
                f"This link expires in {TOKEN_TTL_MINUTES} minutes.",
                "",
                *(
                    ["If you are on your phone, you can also open the app:", app_verification_url, ""]
                    if app_verification_url
                    else []
                ),
                "",
                "If you did not create a PhunParty account, you can ignore this email.",
            ]
        )
    )
    app_link_html = (
        f"""
        <p style="margin: 24px 0 0;">
          <a href="{app_verification_url}" style="color: #0f766e;">Open in the PhunParty app</a>
        </p>
        """
        if app_verification_url
        else ""
    )
    message.add_alternative(
        f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#0f172a;font-family:Arial,sans-serif;color:#f8fafc;">
    <div style="max-width:560px;margin:0 auto;padding:32px 20px;">
      <h1 style="margin:0 0 16px;font-size:28px;">Welcome to PhunParty</h1>
      <p style="font-size:16px;line-height:1.5;color:#cbd5e1;">
        Tap the button below to verify your email and continue.
      </p>
      <p style="margin:28px 0;">
        <a href="{verification_url}" style="display:inline-block;background:#2dd4bf;color:#0f172a;text-decoration:none;font-weight:700;padding:14px 22px;border-radius:10px;">
          Verify Email
        </a>
      </p>
      <p style="font-size:14px;line-height:1.5;color:#94a3b8;">
        This link expires in {TOKEN_TTL_MINUTES} minutes.
      </p>
      {app_link_html}
      <p style="font-size:12px;line-height:1.5;color:#64748b;margin-top:32px;">
        If the button does not work, copy and paste this link into your browser:<br />
        <a href="{verification_url}" style="color:#5eead4;">{verification_url}</a>
      </p>
    </div>
  </body>
</html>
""",
        subtype="html",
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)

    logger.info("Email verification link sent to %s", to_email)
    return True


def send_email_verification_code(to_email: str, code: str) -> bool:
    logger.warning(
        "send_email_verification_code is deprecated; use send_email_verification_link"
    )
    return send_email_verification_link(to_email, code)
