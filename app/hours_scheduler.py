"""Deadline processing for monthly hours submissions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import HOURS_REPORT_RECIPIENT
from app.db import SessionLocal
from app.hours import is_required_member, member_key, previous_month, report_due_date
from app.models import MonthlyHoursCycle, MonthlyHoursSubmission, User
from app.notifications import send_hours_reminder_email

try:
    SAST = ZoneInfo("Africa/Johannesburg")
except Exception:
    # South Africa observes UTC+2 year-round; this keeps Windows startup working
    # until the tzdata dependency is installed.
    SAST = timezone(timedelta(hours=2))


def process_hours_deadline() -> None:
    """Send the noon reminder or consolidated workbook for the current deadline."""
    now = datetime.now(SAST)
    report_month = previous_month(now.date())
    due_date = report_due_date(report_month)
    if now.date() != due_date or (now.hour, now.minute) < (12, 0):
        return

    with SessionLocal() as db:
        cycle = db.get(MonthlyHoursCycle, report_month)
        if not cycle:
            cycle = MonthlyHoursCycle(report_month=report_month)
            db.add(cycle)
            db.commit()

        submissions = list(db.scalars(select(MonthlyHoursSubmission).where(
            MonthlyHoursSubmission.report_month == report_month
        ).options(selectinload(MonthlyHoursSubmission.user))))
        submitted_names = {member_key(submission.user) for submission in submissions}
        missing_users = [
            member for member in db.scalars(select(User).order_by(User.name))
            if is_required_member(member) and member_key(member) not in submitted_names
        ]

        if missing_users and not cycle.reminder_sent:
            send_hours_reminder_email(
                recipients=[member.email for member in missing_users],
                missing_members=[member.name for member in missing_users],
                month=report_month.strftime("%B %Y"),
                due_date=f"{due_date:%d %B %Y} 12:00 SAST",
            )
            cycle.reminder_sent = True
            cycle.reminder_sent_at = datetime.now(timezone.utc)
            db.commit()
