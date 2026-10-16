"""Lightweight JSON endpoints used by the Kanban board's JavaScript."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import Priority, Project, Task, TaskStatus, User, UserRole
from app.notifications import send_task_assignment_email
from app.services import log_activity, recompute_health

router = APIRouter(prefix="/api")


def _can_add_task(user: User, project: Project) -> bool:
    return user.role in (UserRole.admin, UserRole.manager, UserRole.developer)


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
    if not task.can_edit(user):
        return {"ok": False, "error": "Only the person who created this task can edit it."}
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


class EditTaskPayload(BaseModel):
    title: str = Field(max_length=200)
    description: str = ""
    notes: str = ""
    status: str
    priority: str
    due_date: str | None = None
    assignees: list[int] = Field(default_factory=list)


@router.post("/tasks/{task_id}/edit")
def edit_task(task_id: int, payload: EditTaskPayload,
              background_tasks: BackgroundTasks,
              user: User = Depends(require_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        return {"ok": False, "error": "Task not found."}
    if not task.can_edit(user):
        return {"ok": False, "error": "Only the person who created this task can edit it."}
    title = payload.title.strip()
    if not title:
        return {"ok": False, "error": "Task title is required."}

    status = _status_from(payload.status)
    priority = next((p for p in Priority if p.value == payload.priority), None)
    if status is None or priority is None:
        return {"ok": False, "error": "Select a valid status and priority."}

    due = None
    if payload.due_date:
        try:
            due = date.fromisoformat(payload.due_date)
        except ValueError:
            return {"ok": False, "error": "Enter a valid due date."}

    assignee_ids = set(payload.assignees)
    assignees = list(db.scalars(select(User).where(User.id.in_(assignee_ids)))) if assignee_ids else []
    if {assignee.id for assignee in assignees} != assignee_ids:
        return {"ok": False, "error": "One or more selected assignees no longer exist. Refresh and try again."}

    previous_assignee_ids = {assignee.id for assignee in task.assignees}
    old_status = task.status
    task.title = title
    task.description = payload.description.strip()
    task.notes = payload.notes.strip()
    task.status = status
    task.priority = priority
    task.due_date = due
    task.assignees = assignees
    if old_status != status:
        min_order = db.scalar(
            select(func.coalesce(func.min(Task.order), 0)).where(
                Task.project_id == task.project_id, Task.status == status
            )
        )
        task.order = (min_order or 0) - 1
        log_activity(db, user=user, verb="moved",
                     summary=f'moved "{task.title}" to {status.value}',
                     project=task.project, task=task)
        if status == TaskStatus.done:
            log_activity(db, user=user, verb="completed",
                         summary=f'completed "{task.title}"', project=task.project, task=task)
        recompute_health(task.project)
    else:
        log_activity(db, user=user, verb="edited",
                     summary=f'updated "{task.title}"', project=task.project, task=task)
    db.commit()
    for assignee in assignees:
        if assignee.id not in previous_assignee_ids:
            background_tasks.add_task(
                send_task_assignment_email,
                recipient=assignee.email,
                recipient_name=assignee.name,
                task_id=task.id,
                task_title=task.title,
                project_name=task.project.name,
                priority=priority.value,
                due_date=due.strftime("%d %b %Y") if due else "Not set",
                assigned_by=user.name,
            )
    return {"ok": True}


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
    if not _can_add_task(user, project):
        return {"ok": False, "error": "not a member of this project"}
    status = _status_from(payload.status) or TaskStatus.todo
    priority = next((p for p in Priority if p.value == payload.priority), Priority.medium)

    due = None
    if payload.due_date:
        try:
            due = date.fromisoformat(payload.due_date)
        except ValueError:
            due = None

    # Any team member can be assigned, regardless of role or project membership.
    if payload.assignees:
        assignees = list(db.scalars(select(User).where(User.id.in_(payload.assignees))))
        if {assignee.id for assignee in assignees} != set(payload.assignees):
            return {"ok": False, "error": "One or more selected assignees no longer exist. Refresh and try again."}
    else:
        assignees = [user]

    # Columns sort by Task.order ascending, so sorting below the current minimum
    # puts a new task at the top of its column -- next to the "+ Add task" button
    # rather than buried under a long list.
    min_order = db.scalar(
        select(func.coalesce(func.min(Task.order), 0)).where(
            Task.project_id == project.id, Task.status == status
        )
    )
    task = Task(project_id=project.id, title=payload.title.strip(),
                status=status, priority=priority, due_date=due,
                order=(min_order or 0) - 1, assignees=assignees,
                created_by=user.id)
    db.add(task)
    db.flush()
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
            priority=priority.value,
            due_date=due.strftime("%d %b %Y") if due else "Not set",
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
