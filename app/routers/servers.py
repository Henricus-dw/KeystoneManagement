"""
Servers & credentials vault.

A place for the team to record servers, VMs, containers and the details needed
to reach them. Entries are private to their owner until *shared*, at which point
every signed-in user can see them (read-only). Only the owner or an Admin may
edit, share or delete an entry.

Secrets are masked in the UI and never appear in list views or the activity
log. They are, however, stored in clear text (no crypto dependency in this
project) -- see the note on the Server model.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.deps import require_user
from app.models import Server, ServerEnv, ServerKind, User
from app.services import log_activity
from app.templating import templates

router = APIRouter()


def _enum_from(enum_cls, value: str, default):
    return next((m for m in enum_cls if m.value == value), default)


def _port_from(value: str) -> int | None:
    value = (value or "").strip()
    if value.isdigit():
        port = int(value)
        if 0 < port <= 65535:
            return port
    return None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get("/servers")
def servers_list(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(Server)
            .options(selectinload(Server.owner))
            .where(or_(Server.owner_id == user.id, Server.shared.is_(True)))
            .order_by(Server.updated_at.desc())
        )
    )
    mine = [s for s in rows if s.owner_id == user.id]
    shared = [s for s in rows if s.owner_id != user.id]  # shared by someone else
    return templates.TemplateResponse(request, "servers.html", {
        "user": user, "nav": "servers", "mine": mine, "shared": shared,
        "created": request.query_params.get("created") == "1",
    })


# ---------------------------------------------------------------------------
# New / create
# ---------------------------------------------------------------------------
@router.get("/servers/new")
def new_server_form(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse(request, "server_form.html", {
        "user": user, "nav": "servers", "server": None,
        "kinds": list(ServerKind), "envs": list(ServerEnv),
    })


@router.post("/servers")
def create_server(
    name: str = Form(...),
    kind: str = Form("Server"),
    environment: str = Form("Production"),
    host: str = Form(""),
    port: str = Form(""),
    username: str = Form(""),
    secret: str = Form(""),
    url: str = Form(""),
    notes: str = Form(""),
    shared: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return RedirectResponse("/servers/new", status_code=303)

    server = Server(
        name=name.strip(),
        kind=_enum_from(ServerKind, kind, ServerKind.server),
        environment=_enum_from(ServerEnv, environment, ServerEnv.production),
        host=host.strip(),
        port=_port_from(port),
        username=username.strip(),
        secret=secret,  # stored as-is (may contain leading/trailing spaces in a key)
        url=url.strip(),
        notes=notes.strip(),
        shared=bool(shared),
        owner_id=user.id,
    )
    db.add(server)
    db.flush()
    where = "shared with the team" if server.shared else "private"
    log_activity(db, user=user, verb="created",
                 summary=f'added server "{server.name}" ({where})')
    db.commit()
    return RedirectResponse(f"/servers/{server.id}", status_code=303)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
@router.get("/servers/{server_id}")
def server_detail(server_id: int, request: Request,
                  user: User = Depends(require_user), db: Session = Depends(get_db)):
    server = db.get(Server, server_id)
    if not server or not server.can_view(user):
        return RedirectResponse("/servers", status_code=303)
    return templates.TemplateResponse(request, "server_detail.html", {
        "user": user, "nav": "servers", "server": server,
        "can_manage": server.can_manage(user),
    })


# ---------------------------------------------------------------------------
# Edit / update
# ---------------------------------------------------------------------------
@router.get("/servers/{server_id}/edit")
def edit_server_form(server_id: int, request: Request,
                     user: User = Depends(require_user), db: Session = Depends(get_db)):
    server = db.get(Server, server_id)
    if not server:
        return RedirectResponse("/servers", status_code=303)
    if not server.can_manage(user):
        return RedirectResponse(f"/servers/{server_id}", status_code=303)
    return templates.TemplateResponse(request, "server_form.html", {
        "user": user, "nav": "servers", "server": server,
        "kinds": list(ServerKind), "envs": list(ServerEnv),
    })


@router.post("/servers/{server_id}/edit")
def update_server(
    server_id: int,
    name: str = Form(...),
    kind: str = Form("Server"),
    environment: str = Form("Production"),
    host: str = Form(""),
    port: str = Form(""),
    username: str = Form(""),
    secret: str = Form(""),
    url: str = Form(""),
    notes: str = Form(""),
    shared: str = Form(""),
    keep_secret: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    server = db.get(Server, server_id)
    if not server:
        return RedirectResponse("/servers", status_code=303)
    if not server.can_manage(user):
        return RedirectResponse(f"/servers/{server_id}", status_code=303)
    if not name.strip():
        return RedirectResponse(f"/servers/{server_id}/edit", status_code=303)

    server.name = name.strip()
    server.kind = _enum_from(ServerKind, kind, server.kind)
    server.environment = _enum_from(ServerEnv, environment, server.environment)
    server.host = host.strip()
    server.port = _port_from(port)
    server.username = username.strip()
    # Leaving the secret field blank keeps the existing one (so an editor need
    # not retype it); it is only overwritten when a new value is supplied.
    if not keep_secret or secret:
        server.secret = secret
    server.url = url.strip()
    server.notes = notes.strip()
    server.shared = bool(shared)
    log_activity(db, user=user, verb="updated",
                 summary=f'updated server "{server.name}"')
    db.commit()
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


# ---------------------------------------------------------------------------
# Share / unshare
# ---------------------------------------------------------------------------
@router.post("/servers/{server_id}/share")
def toggle_share(server_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    server = db.get(Server, server_id)
    if not server:
        return RedirectResponse("/servers", status_code=303)
    if not server.can_manage(user):
        return RedirectResponse(f"/servers/{server_id}", status_code=303)
    server.shared = not server.shared
    verb = "shared" if server.shared else "updated"
    action = "shared" if server.shared else "made private"
    log_activity(db, user=user, verb=verb,
                 summary=f'{action} server "{server.name}"')
    db.commit()
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@router.post("/servers/{server_id}/delete")
def delete_server(server_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    server = db.get(Server, server_id)
    if not server:
        return RedirectResponse("/servers", status_code=303)
    if not server.can_manage(user):
        return RedirectResponse(f"/servers/{server_id}?error=forbidden", status_code=303)
    name = server.name
    db.delete(server)
    log_activity(db, user=user, verb="deleted", summary=f'deleted server "{name}"')
    db.commit()
    return RedirectResponse("/servers", status_code=303)
