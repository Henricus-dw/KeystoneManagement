"""Lightweight JSON endpoints used by the Kanban board's JavaScript."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import Priority, Project, Task, TaskStatus, User, UserRole
from app.notifications import send_task_assignment_email
from app.services import log_activity, recompute_health

router = APIRouter(prefix="/api")


class MovePayload(BaseModel):
    status: str
    order: int | None = None


def _status_from(value: str) -> TaskStatus | None:
    return next((s for s in TaskStatus if s.value == value), None)


@router.post("/tasks/{task_id}/move")
def move_task(task_id: int, payload: MovePayload,
              user: User = Depends(require_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        return {"ok": False, "error": "not found"}
    new_status = _status_from(payload.status)
    if new_status is None:
        return {"ok": False, "error": "bad status"}

    moved = new_status != task.status
    old_status = task.status
    task.status = new_status
    if payload.order is not None:
        task.order = payload.order

    if moved:
        log_activity(db, user=user, verb="moved",
                     summary=f'moved "{task.title}" to {new_status.value}',
                     project=task.project, task=task)
        if new_status == TaskStatus.done:
            log_activity(db, user=user, verb="completed",
                         summary=f'completed "{task.title}"', project=task.project, task=task)
        recompute_health(task.project)

    db.commit()
    return {
        "ok": True,
        "progress": task.project.progress,
        "health": task.project.health.value,
        "moved": moved,
        "from": old_status.value,
    }


class CreatePayload(BaseModel):
    project_id: int
    title: str
    status: str = "Todo"
    priority: str = "Medium"
    due_date: str | None = None
    assignees: list[int] = []


def _avatar(u: User) -> dict:
    return {"initials": u.initials, "accent": u.accent, "name": u.name}


@router.post("/tasks")
def create_task(payload: CreatePayload,
                background_tasks: BackgroundTasks,
                user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = db.get(Project, payload.project_id)
    if not project or not payload.title.strip():
        return {"ok": False}
    # Developers may only add tasks to projects they're assigned to.
    if user.role == UserRole.developer and user not in project.members:
        return {"ok": False, "error": "not a member of this project"}
    status = _status_from(payload.status) or TaskStatus.todo
    priority = next((p for p in Priority if p.value == payload.priority), Priority.medium)

    due = None
    if payload.due_date:
        try:
            due = date.fromisoformat(payload.due_date)
        except ValueError:
            due = None

    # Resolve assignees; default to the creator so new cards aren't unassigned.
    assignees = list(db.scalars(select(User).where(User.id.in_(payload.assignees)))) \
        if payload.assignees else []
    if not assignees:
        assignees = [user]

    max_order = db.scalar(
        select(func.coalesce(func.max(Task.order), 0)).where(
            Task.project_id == project.id, Task.status == status
        )
    )
    task = Task(project_id=project.id, title=payload.title.strip(),
                status=status, priority=priority, due_date=due,
                order=(max_order or 0) + 1, assignees=assignees)
    db.add(task)
    log_activity(db, user=user, verb="created",
                 summary=f'created task "{task.title}"', project=project, task=task)
    db.commit()
    for assignee in assignees:
        background_tasks.add_task(
            send_task_assignment_email,
            recipient=assignee.email,
            recipient_name=assignee.name,
            task_id=task.id,
            task_title=task.title,
            project_name=project.name,
            assigned_by=user.name,
        )
    return {
        "ok": True,
        "task": {
            "id": task.id, "code": task.code, "title": task.title, "status": status.value,
            "priority": priority.value,
            "due": due.strftime("%d %b") if due else None,
            "assignees": [_avatar(u) for u in assignees],
        },
        "progress": project.progress,
    }
