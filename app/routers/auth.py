"""Login / logout and the root redirect."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.security import verify_password
from app.templating import templates

router = APIRouter()


@router.get("/")
def root(user: User | None = Depends(get_current_user)):
    return RedirectResponse("/dashboard" if user else "/login", status_code=303)


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    wants_json = request.headers.get("x-requested-with") == "fetch"
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not verify_password(password, user.password_hash):
        msg = "Those credentials don't match. Try again."
        if wants_json:
            return JSONResponse({"ok": False, "error": msg}, status_code=401)
        return templates.TemplateResponse(
            request, "login.html", {"error": msg}, status_code=401,
        )
    request.session["user_id"] = user.id
    if wants_json:
        # The browser plays the holographic entrance, then navigates itself.
        return JSONResponse({"ok": True, "next": "/dashboard"})
    # No-JS fallback: ?welcome=1 triggers the entrance on the dashboard instead.
    return RedirectResponse("/dashboard?welcome=1", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
