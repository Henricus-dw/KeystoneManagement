"""Email notifications for Keystone events."""
from __future__ import annotations

import json
import logging
from base64 import b64encode
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.config import (
    APP_BASE_URL,
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_SENDER,
    GRAPH_TENANT_ID,
)

logger = logging.getLogger(__name__)


def _get_access_token() -> str:
    token_url = f"https://login.microsoftonline.com/{quote(GRAPH_TENANT_ID, safe='')}/oauth2/v2.0/token"
    request = Request(
        token_url,
        data=urlencode({
            "client_id": GRAPH_CLIENT_ID,
            "client_secret": GRAPH_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"Microsoft Entra token request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Microsoft Entra token request failed") from exc

    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Microsoft Entra token response did not contain an access token")
    return token


def _send_message(*, recipient: str, subject: str, body: str, kind: str,
                  cc: list[str] | None = None,
                  attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    missing = [
        name for name, value in (
            ("KEYSTONE_GRAPH_TENANT_ID", GRAPH_TENANT_ID),
            ("KEYSTONE_GRAPH_CLIENT_ID", GRAPH_CLIENT_ID),
            ("KEYSTONE_GRAPH_CLIENT_SECRET", GRAPH_CLIENT_SECRET),
            ("KEYSTONE_GRAPH_SENDER", GRAPH_SENDER),
        ) if not value
    ]
    if missing:
        logger.warning("%s email skipped: missing %s", kind, ", ".join(missing))
        return

    try:
        token = _get_access_token()
        send_url = f"https://graph.microsoft.com/v1.0/users/{quote(GRAPH_SENDER, safe='')}/sendMail"
        message = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        }
        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": address}} for address in cc
            ]
        if attachments:
            message["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": filename,
                    "contentType": content_type,
                    "contentBytes": b64encode(contents).decode("ascii"),
                }
                for filename, contents, content_type in attachments
            ]
        request = Request(
            send_url,
            data=json.dumps({
                "message": message,
                "saveToSentItems": True,
            }).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=15) as response:
            if response.status != 202:
                raise RuntimeError(f"Microsoft Graph sendMail returned HTTP {response.status}")
    except HTTPError as exc:
        logger.exception("Could not send %s email to %s: Graph returned HTTP %s", kind.lower(), recipient, exc.code)
    except (URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
        logger.exception("Could not send %s email to %s", kind.lower(), recipient)


def send_task_assignment_email(
    *,
    recipient: str,
    recipient_name: str,
    task_id: int,
    task_title: str,
    project_name: str,
    assigned_by: str,
) -> None:
    """Send a task assignment email through Microsoft Graph."""
    task_url = f"{APP_BASE_URL}/tasks/{task_id}"
    _send_message(
        recipient=recipient,
        subject=f"You have been assigned a task: {task_title}",
        body=(
            f"Hi {recipient_name},\n\n"
            f"{assigned_by} assigned you the task \"{task_title}\" "
            f"in the project \"{project_name}\".\n\n"
            f"View the task: {task_url}\n\n"
            "Keystone"
        ),
        kind="Task assignment",
    )


def send_hours_report_email(*, recipient: str, submitter_email: str,
                            submitter_name: str, month: str, entries: list[dict[str, str]],
                            workbook: bytes, filename: str) -> None:
    """Send a submitted monthly hours workbook through Microsoft Graph."""
    cc = [] if submitter_email.lower() == recipient.lower() else [submitter_email]
    _send_message(
        recipient=recipient,
        cc=cc,
        subject=f"{submitter_name}'s hours for {month}",
        body=(
            f"Hello,\n\n"
            f"IT Tech: {submitter_name}\n"
            f"Please find attached the hours report for {month}.\n\n"
            "The Excel file contains the complete monthly hours submission.\n\n"
        ),
        attachments=[(
            filename,
            workbook,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )],
        kind="Monthly hours",
    )


def send_project_assignment_email(
    *,
    recipient: str,
    recipient_name: str,
    project_id: int,
    project_name: str,
    assigned_by: str,
) -> None:
    """Send a project assignment email through Microsoft Graph."""
    project_url = f"{APP_BASE_URL}/projects/{project_id}"
    _send_message(
        recipient=recipient,
        subject=f"You have been assigned to a project: {project_name}",
        body=(
            f"Hi {recipient_name},\n\n"
            f"{assigned_by} assigned you to the project \"{project_name}\".\n\n"
            f"View the project: {project_url}\n\n"
            "Keystone"
        ),
        kind="Project assignment",
    )
