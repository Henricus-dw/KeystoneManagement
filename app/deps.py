"""Shared request dependencies: current user + auth guards."""
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, UserRole
from app.access import enforce_guest_access


class AuthRedirect(Exception):
    """Raised to bounce an unauthenticated request back to the login screen."""

    def __init__(self, location: str = "/login"):
        self.location = location


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def require_user(request: Request, user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise AuthRedirect()
    enforce_guest_access(request, user)
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.admin:
        raise AuthRedirect("/dashboard")
    return user


def require_manager(user: User = Depends(require_user)) -> User:
    """Oversight views, plus guests explicitly granted read-only page access."""
    if user.role == UserRole.guest:
        return user  # require_user has already checked the page grant and method.
    if user.role not in (UserRole.admin, UserRole.manager):
        raise AuthRedirect("/dashboard")
    return user
