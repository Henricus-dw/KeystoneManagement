"""
Keystone data model.

This expands on Henri's high-level schema in TASKMASTER.pdf and closes a number
of gaps in it (see README "Notes on the original plan"). Key changes:

  * Users carry a password hash + role (the PDF's login system had no auth field).
  * "Assigned developer(s)" is a real many-to-many, not a single AssignedUserId.
  * Project *health* (On Track / At Risk / ...) is modelled separately from
    *status* and from task progress -- the PDF's own closing insight that a
    project can be 80% done and still "At Risk".
  * Tasks, comments, reports and attachments all carry timestamps.
  * Activity events are first-class so the timeline / feed is real data.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class UserRole(str, enum.Enum):
    admin = "Admin"
    manager = "Manager"
    developer = "Developer"


class ProjectStatus(str, enum.Enum):
    planning = "Planning"
    development = "Development"
    testing = "Testing"
    waiting_on_client = "Waiting on Client"
    completed = "Completed"
    maintenance = "Maintenance"  # shipped & live, ongoing patches/support
    on_hold = "On Hold"


class Priority(str, enum.Enum):
    low = "Low"
    medium = "Medium"
    high = "High"
    critical = "Critical"


class ProjectHealth(str, enum.Enum):
    on_track = "On Track"
    at_risk = "At Risk"
    blocked = "Blocked"
    delayed = "Delayed"


class TaskStatus(str, enum.Enum):
    todo = "Todo"
    in_progress = "In Progress"
    blocked = "Blocked"
    testing = "Testing"
    done = "Done"


class ServerKind(str, enum.Enum):
    """What sort of resource an infrastructure entry describes."""

    server = "Server"
    vm = "Virtual Machine"
    container = "Container"
    database = "Database"
    cloud = "Cloud"
    service = "Service"
    other = "Other"


class ServerEnv(str, enum.Enum):
    production = "Production"
    staging = "Staging"
    development = "Development"
    other = "Other"


# ---------------------------------------------------------------------------
# Association tables
# ---------------------------------------------------------------------------
project_members = Table(
    "project_members",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)

task_assignees = Table(
    "task_assignees",
    Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


# ---------------------------------------------------------------------------
# Core entities
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.developer)
    title: Mapped[str] = mapped_column(String(120), default="Developer")
    accent: Mapped[str] = mapped_column(String(7), default="#1CC4D8")  # avatar colour
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    projects: Mapped[list[Project]] = relationship(
        secondary=project_members, back_populates="members"
    )
    tasks: Mapped[list[Task]] = relationship(
        secondary=task_assignees, back_populates="assignees"
    )
    comments: Mapped[list[Comment]] = relationship(back_populates="author")
    reports: Mapped[list[ProgressReport]] = relationship(back_populates="user")

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or self.name[:2].upper()

    @property
    def handle(self) -> str:
        return "@" + self.email.split("@")[0]


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    client: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), default=ProjectStatus.planning)
    priority: Mapped[Priority] = mapped_column(Enum(Priority), default=Priority.medium)
    health: Mapped[ProjectHealth] = mapped_column(Enum(ProjectHealth), default=ProjectHealth.on_track)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    creator: Mapped[User | None] = relationship("User", foreign_keys=[created_by])
    members: Mapped[list[User]] = relationship(
        secondary=project_members, back_populates="projects"
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Task.order"
    )

    # Progress is *derived* from task state rather than a hand-edited number, so
    # it can never drift out of sync with reality.
    @property
    def progress(self) -> int:
        if not self.tasks:
            return 0
        done = sum(1 for t in self.tasks if t.status == TaskStatus.done)
        return round(done / len(self.tasks) * 100)

    @property
    def code(self) -> str:
        return f"KS-{self.id:04d}"

    def count(self, status: TaskStatus) -> int:
        return sum(1 for t in self.tasks if t.status == status)

    @property
    def days_left(self) -> int | None:
        if not self.due_date:
            return None
        return (self.due_date - date.today()).days

    @property
    def is_active(self) -> bool:
        return self.status not in (ProjectStatus.completed, ProjectStatus.on_hold)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.todo)
    priority: Mapped[Priority] = mapped_column(Enum(Priority), default=Priority.medium)
    notes: Mapped[str] = mapped_column(Text, default="")  # PDF: "Add notes option"
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)  # position within a column
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    project: Mapped[Project] = relationship(back_populates="tasks")
    assignees: Mapped[list[User]] = relationship(
        secondary=task_assignees, back_populates="tasks"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    @property
    def code(self) -> str:
        return f"KS-{self.id:04d}"


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    task: Mapped[Task] = relationship(back_populates="comments")
    author: Mapped[User] = relationship(back_populates="comments")


class ProgressReport(Base):
    """A developer's daily stand-up entry (Today / Tomorrow / Blocked)."""

    __tablename__ = "progress_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    report_date: Mapped[date] = mapped_column(Date, default=date.today)
    today: Mapped[str] = mapped_column(Text, default="")
    tomorrow: Mapped[str] = mapped_column(Text, default="")
    blocked: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    user: Mapped[User] = relationship(back_populates="reports")
    project: Mapped[Project | None] = relationship()


class Activity(Base):
    """Timeline / activity-feed event."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    verb: Mapped[str] = mapped_column(String(40))      # e.g. "completed", "moved"
    summary: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    user: Mapped[User | None] = relationship()
    project: Mapped[Project | None] = relationship()


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    filepath: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    task: Mapped[Task] = relationship(back_populates="attachments")


class Server(Base):
    """
    An infrastructure entry -- a server, VM, container, database, etc. -- plus
    the details a developer needs to reach it.

    Access model: a server is *private to its owner* when created. Setting
    ``shared`` makes it visible (read-only) to every signed-in user, so the
    team can use it as a shared reference. Only the owner or an Admin may edit,
    share/unshare, or delete an entry.

    NOTE: ``secret`` (password / key / access token) is stored in clear text --
    there is no crypto dependency in this project. It is masked in the UI and
    kept out of list views and the activity log, but it is NOT encrypted at
    rest. See README for the follow-up to add encryption.
    """

    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[ServerKind] = mapped_column(Enum(ServerKind), default=ServerKind.server)
    environment: Mapped[ServerEnv] = mapped_column(Enum(ServerEnv), default=ServerEnv.production)
    host: Mapped[str] = mapped_column(String(255), default="")       # hostname or IP
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(160), default="")
    secret: Mapped[str] = mapped_column(Text, default="")            # password / key (see note)
    url: Mapped[str] = mapped_column(String(400), default="")        # panel / management URL
    notes: Mapped[str] = mapped_column(Text, default="")
    shared: Mapped[bool] = mapped_column(default=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    owner: Mapped[User | None] = relationship("User", foreign_keys=[owner_id])

    @property
    def code(self) -> str:
        return f"SV-{self.id:04d}"

    @property
    def address(self) -> str:
        """host[:port] for display, or an empty string if no host is set."""
        if not self.host:
            return ""
        return f"{self.host}:{self.port}" if self.port else self.host

    def can_view(self, user: User) -> bool:
        return self.shared or self.owner_id == user.id or user.role == UserRole.admin

    def can_manage(self, user: User) -> bool:
        """Edit / share / delete -- owner or Admin only."""
        return self.owner_id == user.id or user.role == UserRole.admin
