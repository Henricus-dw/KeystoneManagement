"""
Patch notes for Keystone.

Releases live in the database and are managed by admins from the "What's new"
page. SEED_RELEASES is read only once: when the changelog table is first
created, these releases (which shipped before the in-app editor existed) are
copied into it. Editing this list after that has no effect on an existing
database.

Keep the prose plain: no em dashes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ChangeTag, ChangelogChange, ChangelogRelease

# Latest release date first; the more recently created entry wins a same-day tie.
NEWEST_FIRST = (ChangelogRelease.release_date.desc(), ChangelogRelease.id.desc())

SEED_RELEASES: list[dict] = [
    {
        "version": "1.1",
        "date": date(2026, 9, 7),
        "title": "Servers vault and personal touches",
        "summary": "A shared home for infrastructure details, plus a colour of your own.",
        "changes": [
            ("New", "Servers & Access: a place to record servers, VMs, containers "
                    "and databases together with the details needed to reach them. "
                    "Every entry is private to you until you choose to share it with "
                    "the team, and only its owner or an admin can edit or delete it."),
            ("New", "One-click copy on every connection detail, and a show/hide "
                    "toggle on stored passwords so they stay masked until you need them."),
            ("New", "Servers now appear as blocks, each with an optional cover image "
                    "you can upload from the server's page."),
            ("New", "Personal avatar colour: pick your own under Account settings."),
            ("New", "This 'What's new' page, reachable from the sidebar."),
        ],
    },
    {
        "version": "1.0",
        "date": date(2026, 9, 4),
        "title": "Keystone launch",
        "summary": "The first release of Keystone, the project-execution dashboard "
                   "for Asimotech's directors and dev team.",
        "changes": [
            ("New", "Dashboard with overall completion, projects in flight, "
                    "at-risk counts and a live team activity feed."),
            ("New", "Projects with progress derived from task state, and project "
                    "health tracked separately from progress (a project can be "
                    "80% done and still at risk)."),
            ("New", "Kanban board with drag-and-drop across Todo, In Progress, "
                    "Blocked, Testing and Done, with live progress recalculation."),
            ("New", "Team View, Daily Reports (Today / Tomorrow / Blocked) and "
                    "Analytics with a weekly summary per project."),
            ("New", "A permanent activity log with PDF export, for managers and admins."),
            ("New", "Role-based access for Admins, Managers and Developers, with a "
                    "holographic sign-in entrance."),
        ],
    },
]


def seed_changelog(db: Session) -> None:
    """Copy SEED_RELEASES into a freshly created changelog table."""
    for release in SEED_RELEASES:
        db.add(ChangelogRelease(
            version=release["version"],
            release_date=release["date"],
            title=release["title"],
            summary=release["summary"],
            changes=[
                ChangelogChange(position=i, tag=ChangeTag(tag), text=text)
                for i, (tag, text) in enumerate(release["changes"])
            ],
        ))


def latest_version() -> str:
    """Version of the newest release, for the sidebar badge ("" if there are none)."""
    with SessionLocal() as db:
        return db.scalar(
            select(ChangelogRelease.version).order_by(*NEWEST_FIRST).limit(1)
        ) or ""
