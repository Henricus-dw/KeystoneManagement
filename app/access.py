"""Page grants for read-only guests; staff role permissions remain unchanged."""
from typing import Literal

from fastapi import HTTPException, Request

from app.backups import LEARNING_PLATFORMS, OTHER_PLATFORMS
from app.models import User, UserRole

GuestPage = Literal[
    "dashboard", "projects", "board", "servers", "backups-learning", "backups-apps",
    "team", "progress", "reports", "activity", "hours", "changelog",
]
GUEST_PAGES: dict[str, tuple[str, str]] = {
    "dashboard": ("Dashboard", "/dashboard"),
    "projects": ("Projects (including project details and files)", "/projects"),
    "board": ("Kanban (including task details and files)", "/board"),
    "servers": ("Servers (including visible server credentials)", "/servers"),
    "backups-learning": ("E-Learning / Moodle Backups", "/backups/learning"),
    "backups-apps": ("Other App Backups", "/backups/apps"),
    "team": ("Team View", "/team"),
    "progress": ("Daily Reports", "/progress"),
    "reports": ("Analytics", "/reports"),
    "activity": ("Activity (including PDF export)", "/activity"),
    "hours": ("Hours Tracker (including workbook review)", "/hours-tracker"),
    "changelog": ("What's new", "/changelog"),
}


def can_access_page(user: User, page: str) -> bool:
    if user.role == UserRole.guest:
        return page in GUEST_PAGES and page in (user.guest_pages or [])
    if page in ("reports", "activity"):
        return user.role in (UserRole.admin, UserRole.manager)
    if page == "admin":
        return user.role == UserRole.admin
    return True


def landing_page(user: User) -> str:
    if user.role != UserRole.guest:
        return "/dashboard"
    return next((url for key, (_, url) in GUEST_PAGES.items() if can_access_page(user, key)), "/account")


def guest_platforms(user: User) -> tuple[str, ...] | None:
    if user.role != UserRole.guest:
        return None
    return (
        (LEARNING_PLATFORMS if can_access_page(user, "backups-learning") else ())
        + (OTHER_PLATFORMS if can_access_page(user, "backups-apps") else ())
    )


def enforce_guest_access(request: Request, user: User) -> None:
    if user.role != UserRole.guest:
        return
    path = request.url.path.rstrip("/")
    if path == "/account" and request.method in ("GET", "HEAD"):
        return
    if path in ("/account/password", "/account/color") and request.method == "POST":
        return
    if request.method not in ("GET", "HEAD"):
        raise HTTPException(403, "Guests have read-only access.")
    if path in ("/backups", "/api/backups"):
        if guest_platforms(user):
            return
    elif path.startswith("/tasks/"):
        if can_access_page(user, "board") or can_access_page(user, "projects"):
            return
    else:
        for key, (_, url) in GUEST_PAGES.items():
            if path == url or path.startswith(url + "/"):
                if path.endswith(("/new", "/edit")):
                    break
                if can_access_page(user, key):
                    return
                break
    raise HTTPException(403, "Your administrator has not granted access to this page.")
