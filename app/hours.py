"""South African working-day rules for monthly hours reporting."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from io import BytesIO
import json

import holidays


SAST = "Africa/Johannesburg"
REQUIRED_MEMBER_EMAILS = {
    "henri@professionkal.za.com",
    "darryl@professional.aero",
    "jean@professional.za.com",
    "taskeen@professional.za.com",
}


def south_african_holidays(year: int):
    return holidays.SouthAfrica(years=[year])


def is_working_day(day: date) -> bool:
    return day.weekday() < 5 and day not in south_african_holidays(day.year)


def first_working_day(day: date) -> date:
    while not is_working_day(day):
        day += timedelta(days=1)
    return day


def report_due_date(report_month: date) -> date:
    next_month = (report_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_working_day(next_month)


def working_hours(report_month: date) -> int:
    total = 0
    for day_number in range(1, monthrange(report_month.year, report_month.month)[1] + 1):
        if is_working_day(date(report_month.year, report_month.month, day_number)):
            total += 8
    return total


def previous_month(day: date) -> date:
    first = day.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def build_consolidated_workbook(report_month: date, submissions) -> bytes:
    """Build one worksheet per submitted team member."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    workbook.remove(workbook.active)
    headers = ["Internal customer", "Duration (hours)", "Description", "Month", "Year", "IT Tech"]
    for submission in sorted(submissions, key=lambda item: item.user.name.casefold()):
        sheet = workbook.create_sheet(title=submission.user.name[:31])
        sheet.append(headers)
        for entry in json.loads(submission.entries):
            customer = entry["other_customer"] if entry["customer"] == "Other" else entry["customer"]
            sheet.append([
                customer,
                float(entry["duration"]),
                entry["description"],
                report_month.strftime("%B"),
                report_month.year,
                submission.user.name,
            ])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17324D")
        for column, width in zip("ABCDEF", (28, 18, 55, 18, 12, 24)):
            sheet.column_dimensions[column].width = width
        sheet.freeze_panes = "A2"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()