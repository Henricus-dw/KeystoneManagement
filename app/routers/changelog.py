"""
What's new: the patch notes page, and the admin tools to add, edit and delete
releases on it.

Everyone signed in can read the page. Only Admins can change it, the same as
the other admin screens.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.changelog import NEWEST_FIRST
from app.db import get_db
from app.deps import require_admin, require_user
from app.models import ChangeTag, ChangelogChange, ChangelogRelease, User, UserRole
from app.services import log_activity
from app.templating import templates

router = APIRouter()

BLANK_CHANGE = ("New", "")


def _tag_from(value: str) -> ChangeTag:
    return next((t for t in ChangeTag if t.value == value), ChangeTag.new)


def _form_from(release: ChangelogRelease | None) -> dict:
    """Initial form values: blank for a new release, current values for an edit."""
    if release is None:
        return {"version": "", "release_date": date.today().isoformat(),
                "title": "", "summary": "", "changes": [BLANK_CHANGE]}
    return {
        "version": release.version,
        "release_date": release.release_date.isoformat(),
        "title": release.title,
        "summary": release.summary,
        "changes": [(c.tag.value, c.text) for c in release.changes] or [BLANK_CHANGE],
    }


def _validate(db: Session, *, version: str, release_date: str, title: str, summary: str,
              change_tag: list[str], change_text: list[str],
              release_id: int | None = None) -> tuple[dict, date | None, str | None]:
    """
    Clean a submitted release. Returns (values to re-display, parsed date,
    error message); the error is None when the release is valid.
    """
    form = {
        # The page already prints the "v", so accept "v1.2" and store "1.2".
        "version": version.strip().lstrip("vV").strip(),
        "release_date": release_date.strip(),
        "title": title.strip(),
        "summary": summary.strip(),
        # Each change arrives as a tag and a text at the same index; blank lines are dropped.
        "changes": [(tag, text.strip()) for tag, text in zip(change_tag, change_text) if text.strip()],
    }
    try:
        when = date.fromisoformat(form["release_date"])
    except ValueError:
        when = None

    error = None
    if not form["version"]:
        error = "Give the release a version number, for example 1.2."
    elif len(form["version"]) > 20:
        error = "Keep the version number under 20 characters."
    elif when is None:
        error = "Pick a release date."
    elif not form["title"]:
        error = "Give the release a title."
    elif not form["changes"]:
        error = "Add at least one change."
    else:
        clash = db.scalar(select(ChangelogRelease).where(ChangelogRelease.version == form["version"]))
        if clash and clash.id != release_id:
            error = (f"v{form['version']} already exists. Edit that release instead, "
                     "or use a different number.")

    if not form["changes"]:
        form["changes"] = [BLANK_CHANGE]  # always leave a row to type into
    return form, when, error


def _changes_from(pairs: list[tuple[str, str]]) -> list[ChangelogChange]:
    return [ChangelogChange(position=i, tag=_tag_from(tag), text=text)
            for i, (tag, text) in enumerate(pairs)]


def _render_form(request: Request, user: User, release: ChangelogRelease | None,
                 form: dict, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(request, "changelog_form.html", {
        "user": user, "nav": "changelog", "release": release, "form": form,
        "tags": list(ChangeTag), "error": error,
    }, status_code=status_code)


# ---------------------------------------------------------------------------
# The page itself (any signed-in user)
# ---------------------------------------------------------------------------
@router.get("/changelog")
def changelog_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    releases = list(db.scalars(
        select(ChangelogRelease)
        .options(selectinload(ChangelogRelease.changes))
        .order_by(*NEWEST_FIRST)
    ))
    return templates.TemplateResponse(request, "changelog.html", {
        "user": user, "nav": "changelog", "releases": releases,
        "can_manage": user.role == UserRole.admin,
        "done": request.query_params.get("done"),
    })


# ---------------------------------------------------------------------------
# New / create (Admins only)
# ---------------------------------------------------------------------------
@router.get("/changelog/new")
def new_release_form(request: Request, user: User = Depends(require_admin)):
    return _render_form(request, user, None, _form_from(None))


@router.post("/changelog")
def create_release(
    request: Request,
    version: str = Form(""),
    release_date: str = Form(""),
    title: str = Form(""),
    summary: str = Form(""),
    change_tag: list[str] = Form([]),
    change_text: list[str] = Form([]),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form, when, error = _validate(db, version=version, release_date=release_date, title=title,
                                  summary=summary, change_tag=change_tag, change_text=change_text)
    if error:
        return _render_form(request, user, None, form, error, status_code=400)

    release = ChangelogRelease(version=form["version"], release_date=when, title=form["title"],
                               summary=form["summary"], changes=_changes_from(form["changes"]))
    db.add(release)
    db.flush()
    log_activity(db, user=user, verb="created",
                 summary=f"published the release notes for v{release.version}")
    db.commit()
    return RedirectResponse(f"/changelog?done=created#release-{release.id}", status_code=303)


# ---------------------------------------------------------------------------
# Edit / delete (Admins only)
# ---------------------------------------------------------------------------
@router.get("/changelog/{release_id}/edit")
def edit_release_form(release_id: int, request: Request,
                      user: User = Depends(require_admin), db: Session = Depends(get_db)):
    release = db.get(ChangelogRelease, release_id)
    if not release:
        return RedirectResponse("/changelog", status_code=303)
    return _render_form(request, user, release, _form_from(release))


@router.post("/changelog/{release_id}/edit")
def update_release(
    release_id: int,
    request: Request,
    version: str = Form(""),
    release_date: str = Form(""),
    title: str = Form(""),
    summary: str = Form(""),
    change_tag: list[str] = Form([]),
    change_text: list[str] = Form([]),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    release = db.get(ChangelogRelease, release_id)
    if not release:
        return RedirectResponse("/changelog", status_code=303)
    form, when, error = _validate(db, version=version, release_date=release_date, title=title,
                                  summary=summary, change_tag=change_tag, change_text=change_text,
                                  release_id=release.id)
    if error:
        return _render_form(request, user, release, form, error, status_code=400)

    release.version = form["version"]
    release.release_date = when
    release.title = form["title"]
    release.summary = form["summary"]
    release.changes = _changes_from(form["changes"])  # delete-orphan drops the old lines
    log_activity(db, user=user, verb="updated",
                 summary=f"edited the release notes for v{release.version}")
    db.commit()
    return RedirectResponse(f"/changelog?done=updated#release-{release.id}", status_code=303)


@router.post("/changelog/{release_id}/delete")
def delete_release(release_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    release = db.get(ChangelogRelease, release_id)
    if release:
        version = release.version
        db.delete(release)
        log_activity(db, user=user, verb="deleted",
                     summary=f"deleted the release notes for v{version}")
        db.commit()
    return RedirectResponse("/changelog?done=deleted", status_code=303)
