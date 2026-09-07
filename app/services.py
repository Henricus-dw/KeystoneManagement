"""Small domain helpers shared by routers."""
from sqlalchemy.orm import Session

from app.models import Activity, Project, ProjectHealth, ProjectStatus, Task, TaskStatus, User


def log_activity(
    db: Session,
    *,
    user: User | None,
    verb: str,
    summary: str,
    project: Project | None = None,
    task: Task | None = None,
) -> Activity:
    """Record a timeline event. Caller is responsible for committing."""
    act = Activity(
        user_id=user.id if user else None,
        project_id=project.id if project else None,
        task_id=task.id if task else None,
        verb=verb,
        summary=summary,
    )
    db.add(act)
    return act


def recompute_health(project: Project) -> None:
    """
    Derive a sensible default health signal.

    Health is intentionally separate from progress: a project that is mostly
    done can still be At Risk if work is blocked or the deadline has passed.
    Managers can always override this manually on the project.
    """
    if project.status == ProjectStatus.completed:
        project.health = ProjectHealth.on_track
        return

    blocked = project.count(TaskStatus.blocked)

    # Maintenance projects are live & ongoing -- there's no delivery deadline,
    # so health is driven purely by whether patch work is blocked.
    if project.status == ProjectStatus.maintenance:
        project.health = ProjectHealth.at_risk if blocked else ProjectHealth.on_track
        return

    days_left = project.days_left

    if project.status == ProjectStatus.waiting_on_client or blocked >= 2:
        project.health = ProjectHealth.blocked
    elif days_left is not None and days_left < 0 and project.progress < 100:
        project.health = ProjectHealth.delayed
    elif (days_left is not None and days_left <= 3 and project.progress < 75) or blocked == 1:
        project.health = ProjectHealth.at_risk
    else:
        project.health = ProjectHealth.on_track
