"""
Patch notes / changelog for Keystone.

This is the single source of truth for the "What's new" page. To add a release,
prepend a new entry to CHANGELOG (newest first). Each change is a
(tag, text) pair; tag is one of: "New", "Improved", "Fixed".

Keep the prose plain: no em dashes.
"""
from __future__ import annotations

CHANGELOG: list[dict] = [
    {
        "version": "1.1",
        "date": "7 September 2026",
        "title": "Servers vault and personal touches",
        "summary": "A shared home for infrastructure details, plus a colour of your own.",
        "changes": [
            ("New", "Servers & Access: a place to record servers, VMs, containers "
                    "and databases together with the details needed to reach them. "
                    "Every entry is private to you until you choose to share it with "
                    "the team, and only its owner or an admin can edit or delete it."),
            ("New", "One-click copy on every connection detail, and a show/hide "
                    "toggle on stored passwords so they stay masked until you need them."),
            ("New", "Personal avatar colour: pick your own under Account settings."),
            ("New", "This 'What's new' page, reachable from the sidebar."),
        ],
    },
    {
        "version": "1.0",
        "date": "4 September 2026",
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

# The current version is whatever sits at the top of the changelog.
APP_VERSION: str = CHANGELOG[0]["version"]
