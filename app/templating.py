"""Jinja2 environment shared by all routers."""
from datetime import date, datetime, timezone

from fastapi.templating import Jinja2Templates

from app.changelog import latest_version
from app.config import APP_NAME, APP_TAGLINE, STATIC_DIR, TEMPLATES_DIR
from app.models import (
    Priority,
    ProjectHealth,
    ProjectStatus,
    TaskStatus,
    UserRole,
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def timeago(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - value
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m} min ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = secs // 86400
    if d == 1:
        return "yesterday"
    if d < 7:
        return f"{d} days ago"
    return value.strftime("%d %b")


def shortdate(value: date | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d %b")


def longdate(value: date | None) -> str:
    """'7 September 2026'. Built by hand because %-d isn't supported on Windows."""
    if value is None:
        return ""
    return f"{value.day} {value.strftime('%B %Y')}"


def clocktime(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%H:%M")


# CSS slugs used to colour badges/columns. Keyed by the enum *value*.
def slug(value) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    return raw.lower().replace(" ", "-")


def stamp(value: datetime | None) -> str:
    """Full local timestamp for the audit log, e.g. '29 Jun 2026 · 14:32'."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d %b %Y · %H:%M")


templates.env.filters["timeago"] = timeago
templates.env.filters["shortdate"] = shortdate
templates.env.filters["longdate"] = longdate
templates.env.filters["clocktime"] = clocktime
templates.env.filters["stamp"] = stamp
templates.env.filters["slug"] = slug

def _asset_ver(filename: str) -> str:
    """Cache-busting token from a static file's mtime, so browsers refetch on change."""
    try:
        return str(int((STATIC_DIR / filename).stat().st_mtime))
    except OSError:
        return "1"


templates.env.globals["asset_ver"] = _asset_ver

templates.env.globals.update(
    APP_NAME=APP_NAME,
    APP_TAGLINE=APP_TAGLINE,
    # A function, called on each render rather than fixed at startup, so a
    # release published from the admin page shows in the sidebar straight away.
    app_version=latest_version,
    ProjectStatus=ProjectStatus,
    TaskStatus=TaskStatus,
    Priority=Priority,
    ProjectHealth=ProjectHealth,
    UserRole=UserRole,
    TASK_COLUMNS=list(TaskStatus),
    today=date.today,
)
