"""Shared request dependencies: current user + auth guards."""
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, UserRole


class AuthRedirect(Exception):
    """Raised to bounce an unauthenticated request back to the login screen."""

    def __init__(self, location: str = "/login"):
        self.location = location


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise AuthRedirect()
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.admin:
        raise AuthRedirect("/dashboard")
    return user


def require_manager(user: User = Depends(require_user)) -> User:
    """Admins and Managers only (oversight views like the activity log)."""
    if user.role not in (UserRole.admin, UserRole.manager):
        raise AuthRedirect("/dashboard")
    return user
