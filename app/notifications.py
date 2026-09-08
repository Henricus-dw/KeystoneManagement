"""Email notifications for Keystone events."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from app.config import (
    APP_BASE_URL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
)

logger = logging.getLogger(__name__)


def send_task_assignment_email(
    *,
    recipient: str,
    recipient_name: str,
    task_id: int,
    task_title: str,
    project_name: str,
    assigned_by: str,
) -> None:
    """Send a task assignment email, skipping delivery when SMTP is not configured."""
    if not SMTP_HOST:
        logger.warning("Task assignment email skipped: KEYSTONE_SMTP_HOST is not configured")
        return

    task_url = f"{APP_BASE_URL}/tasks/{task_id}"
    message = EmailMessage()
    message["Subject"] = f"You have been assigned a task: {task_title}"
    message["From"] = SMTP_FROM
    message["To"] = recipient
    message.set_content(
        f"Hi {recipient_name},\n\n"
        f"{assigned_by} assigned you the task \"{task_title}\" "
        f"in the project \"{project_name}\".\n\n"
        f"View the task: {task_url}\n\n"
        "Keystone"
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            if os.getenv("KEYSTONE_SMTP_USE_TLS", "true").lower() not in {"0", "false", "no"}:
                smtp.starttls()
                smtp.ehlo()
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)
    except Exception:
        logger.exception("Could not send task assignment email to %s", recipient)
