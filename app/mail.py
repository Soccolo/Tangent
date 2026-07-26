"""Outbound email.

Deliberately stdlib-only: SMTP works with every provider (Resend, Postmark,
Fastmail, Gmail) and adds no dependency. With no SMTP configured the message is
logged instead of sent, so password reset is testable locally and never
silently fails in a way that leaves a user stranded.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import MAIL_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER

log = logging.getLogger("tangent.mail")


def configured() -> bool:
    return bool(SMTP_HOST)


def send(to: str, subject: str, body: str) -> bool:
    """Send a plain-text message. Returns True if it actually went out."""
    if not configured():
        log.warning(
            "SMTP not configured — message for %s not sent.\n"
            "--- subject: %s\n%s\n--- end",
            to, subject, body,
        )
        return False

    message = EmailMessage()
    message["From"] = MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as smtp:
                if SMTP_USER:
                    smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if SMTP_USER:
                    smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(message)
        return True
    except Exception:
        # Never let a mail failure surface as a 500 to the user — the caller
        # returns the same response either way to avoid leaking who exists.
        log.exception("Failed to send mail to %s", to)
        return False
