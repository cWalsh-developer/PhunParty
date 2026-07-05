import hashlib
import logging
import os
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode

import requests
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


def _build_email_verification_content(token: str) -> tuple[str, str, str]:
    verification_url = build_email_verification_url(token)
    app_verification_url = build_app_email_verification_url(token)

    subject = "Verify your PhunParty email"
    text_body = "\n".join(
        [
            "Welcome to PhunParty!",
            "",
            "Verify your email by opening this link:",
            verification_url,
            "",
            f"This link expires in {TOKEN_TTL_MINUTES} minutes.",
            "",
            *(
                [
                    "If you are on your phone, you can also open the app:",
                    app_verification_url,
                    "",
                ]
                if app_verification_url
                else []
            ),
            "",
            "If you did not create a PhunParty account, you can ignore this email.",
        ]
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
    html_body = f"""\
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
"""
    return subject, text_body, html_body


def _from_header(from_email: str, from_name: str) -> str:
    return f"{from_name} <{from_email}>"


def _send_with_smtp(
    to_email: str,
    from_email: str,
    from_name: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false"

    if not smtp_host:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _from_header(from_email, from_name)
    message["To"] = to_email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)

    return True


def _send_with_resend(
    to_email: str,
    from_email: str | None,
    from_name: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> bool:
    resend_api_key = os.getenv("RESEND_API_KEY")
    if not resend_api_key:
        return False

    resend_from = os.getenv("RESEND_FROM") or (
        _from_header(from_email, from_name) if from_email else None
    )
    if not resend_from:
        logger.warning("RESEND_API_KEY is set, but RESEND_FROM is missing")
        return False

    logger.info("Sending email verification link via Resend to %s", to_email)
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": resend_from,
            "to": [to_email],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        },
        timeout=10,
    )
    if response.ok:
        logger.info("Resend accepted email verification link for %s", to_email)
        return True

    logger.error(
        "Resend email request failed with status %s: %s",
        response.status_code,
        response.text[:500],
    )
    return False


def send_email_verification_link(to_email: str, token: str) -> bool:
    smtp_username = os.getenv("SMTP_USERNAME")
    from_email = (
        os.getenv("SMTP_FROM_EMAIL")
        or os.getenv("RESEND_FROM_EMAIL")
        or smtp_username
    )
    from_name = os.getenv("SMTP_FROM_NAME", "PhunParty")
    has_smtp = bool(os.getenv("SMTP_HOST"))
    has_resend = bool(os.getenv("RESEND_API_KEY"))

    if not from_email and not os.getenv("RESEND_FROM"):
        logger.warning(
            "Email verification sender is not configured. Verification link for %s is %s",
            to_email,
            build_email_verification_url(token),
        )
        return False

    subject, text_body, html_body = _build_email_verification_content(token)

    sent = False
    if has_smtp and from_email:
        try:
            sent = _send_with_smtp(
                to_email,
                from_email,
                from_name,
                subject,
                text_body,
                html_body,
            )
        except Exception:
            logger.exception("SMTP email verification send failed")

    if not sent and has_resend:
        sent = _send_with_resend(
            to_email,
            from_email,
            from_name,
            subject,
            text_body,
            html_body,
        )

    if not sent:
        logger.warning(
            "No email verification provider sent a message. Verification link for %s is %s",
            to_email,
            build_email_verification_url(token),
        )
        return False

    logger.info("Email verification link sent to %s", to_email)
    return True


def send_email_verification_code(to_email: str, code: str) -> bool:
    logger.warning(
        "send_email_verification_code is deprecated; use send_email_verification_link"
    )
    return send_email_verification_link(to_email, code)
