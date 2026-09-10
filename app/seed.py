"""Create tables and, on first run, populate a believable demo dataset."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import (
    Activity,
    Comment,
    Priority,
    Project,
    ProgressReport,
    ProjectHealth,
    ProjectStatus,
    Task,
    TaskStatus,
    User,
    UserRole,
)
from app.security import hash_password
from app.services import recompute_health

DEFAULT_PASSWORD = "keystone"


def _dt(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Lightweight, idempotent schema migrations for existing databases."""
    with engine.begin() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(projects)")]
        if "created_by" not in cols:
            conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN created_by INTEGER")
            # Backfill creator from the "created project" activity where we can.
            conn.exec_driver_sql(
                """
                UPDATE projects SET created_by = (
                    SELECT a.user_id FROM activities a
                    WHERE a.project_id = projects.id AND a.verb = 'created'
                      AND a.summary LIKE 'created project%'
                    ORDER BY a.created_at ASC LIMIT 1
                )
                WHERE created_by IS NULL
                """
            )

        # servers.image -- added when server blocks gained a cover image.
        tables = [r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        if "servers" in tables:
            scols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(servers)")]
            if "image" not in scols:
                conn.exec_driver_sql(
                    "ALTER TABLE servers ADD COLUMN image VARCHAR(300) NOT NULL DEFAULT ''")

        if "monthly_hours_cycles" in tables:
            hcols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(monthly_hours_cycles)")]
            if "workbook_path" not in hcols:
                conn.exec_driver_sql(
                    "ALTER TABLE monthly_hours_cycles ADD COLUMN workbook_path VARCHAR(500)")


def seed() -> None:
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(User).limit(1)):
            taskeen = db.scalar(select(User).where(User.email == "taskeen@professional.za.com"))
            if not taskeen:
                db.add(User(
                    name="Taskeen Vallee",
                    email="taskeen@professional.za.com",
                    password_hash=hash_password(DEFAULT_PASSWORD),
                    role=UserRole.developer,
                    title="Manager",
                    accent="#7AF5C7",
                ))
                db.commit()
            elif taskeen.title != "Manager":
                taskeen.title = "Manager"
                db.commit()
            return  # already seeded

        # -- People ---------------------------------------------------------
        jean = User(name="Jean Mercier", email="jean@professional.za.com",
                    password_hash=hash_password(DEFAULT_PASSWORD),
                    role=UserRole.admin, title="Engineering Lead", accent="#1CC4D8")
        adele = User(name="Adele Brandt", email="adele@professional.za.com",
                     password_hash=hash_password(DEFAULT_PASSWORD),
                     role=UserRole.manager, title="Delivery Manager", accent="#C9A227")
        henri = User(name="Henri Bauer", email="henri@professionkal.za.com",
                     password_hash=hash_password(DEFAULT_PASSWORD),
                     role=UserRole.developer, title="Junior Developer", accent="#3FE0C2")
        darryl = User(name="Darryl Okonkwo", email="darryl@professional.aero",
                      password_hash=hash_password(DEFAULT_PASSWORD),
                      role=UserRole.developer, title="Full-stack Developer", accent="#7C9CF5")
        taskeen = User(name="Taskeen Vallee", email="taskeen@professional.za.com",
                       password_hash=hash_password(DEFAULT_PASSWORD),
                       role=UserRole.developer, title="Manager", accent="#7AF5C7")
        db.add_all([jean, adele, henri, darryl, taskeen])
        db.flush()

        # -- Projects -------------------------------------------------------
        portal = Project(
            name="Client Portal", client="Northwind Logistics",
            description="Self-service portal where Northwind's clients track shipments, "
                        "invoices and support tickets.",
            status=ProjectStatus.development, priority=Priority.high,
            start_date=date(2026, 4, 14), due_date=date(2026, 7, 12),
            members=[henri, darryl], created_at=_dt(60),
        )
        alpha = Project(
            name="Project Alpha", client="Internal R&D",
            description="Next-generation analytics engine and reporting layer.",
            status=ProjectStatus.development, priority=Priority.high,
            start_date=date(2026, 3, 2), due_date=date(2026, 7, 3),
            members=[darryl, adele, henri], created_at=_dt(90),
        )
        mobile = Project(
            name="Mobile Companion", client="Northwind Logistics",
            description="iOS/Android companion app for drivers and warehouse staff.",
            status=ProjectStatus.testing, priority=Priority.medium,
            start_date=date(2026, 2, 1), due_date=date(2026, 6, 30),
            members=[adele], created_at=_dt(120),
        )
        billing = Project(
            name="Billing Engine", client="Vanta Finance",
            description="Usage-based billing and invoicing service with Stripe integration.",
            status=ProjectStatus.waiting_on_client, priority=Priority.critical,
            start_date=date(2026, 5, 1), due_date=date(2026, 6, 22),
            members=[darryl], created_at=_dt(40),
        )
        brand = Project(
            name="Brand Refresh Site", client="Asimotech",
            description="Marketing site rebuild on the new design system.",
            status=ProjectStatus.completed, priority=Priority.low,
            start_date=date(2026, 1, 10), due_date=date(2026, 5, 30),
            members=[adele, henri], created_at=_dt(160),
        )
        scoping = Project(
            name="Warehouse Sync", client="Northwind Logistics",
            description="Real-time inventory sync between warehouse scanners and the portal.",
            status=ProjectStatus.planning, priority=Priority.medium,
            start_date=date(2026, 6, 20), due_date=date(2026, 9, 1),
            members=[henri, darryl], created_at=_dt(6),
        )
        db.add_all([portal, alpha, mobile, billing, brand, scoping])
        db.flush()

        # -- Tasks ----------------------------------------------------------
        def make(project, title, status, assignees, prio=Priority.medium,
                 due=None, desc="", notes="", order=0, age=10):
            t = Task(project_id=project.id, title=title, status=status,
                     priority=prio, due_date=due, description=desc, notes=notes,
                     order=order, assignees=assignees,
                     created_at=_dt(age), updated_at=_dt(age / 2))
            db.add(t)
            return t

        T = TaskStatus
        # Client Portal -- spread that lands near 72% done.
        make(portal, "Authentication", T.todo, [henri], Priority.high, date(2026, 7, 1), order=0,
             desc="Email/password + Entra ID SSO for portal clients.")
        make(portal, "Payments integration", T.todo, [darryl], Priority.high, date(2026, 7, 5), order=1,
             desc="Stripe checkout and webhook reconciliation.")
        make(portal, "Dashboard widgets", T.in_progress, [henri], Priority.medium, date(2026, 6, 28), order=0,
             notes="Waiting on final copy from client for the empty states.")
        make(portal, "Reporting module", T.in_progress, [darryl], Priority.medium, date(2026, 6, 29), order=1)
        make(portal, "Mobile login screen", T.testing, [adele], Priority.medium, date(2026, 6, 27), order=0)
        for i, name in enumerate([
            "Shipment tracking view", "Invoice list", "Support ticket form",
            "Account settings", "Email notifications", "Audit log",
            "Role-based permissions", "Landing page", "Search & filters",
        ]):
            make(portal, name, T.done, [henri if i % 2 else darryl], order=i, age=20 + i)

        # Project Alpha -- 12 done / 7 in progress / 2 blocked (mirrors the PDF report).
        for i in range(12):
            make(alpha, f"Pipeline stage {i + 1}", T.done, [darryl if i % 2 else adele], order=i, age=30 + i)
        for i in range(7):
            make(alpha, f"Query builder part {i + 1}", T.in_progress,
                 [adele if i % 2 else henri], order=i, age=8)
        make(alpha, "External data connector", T.blocked, [darryl], Priority.high, order=0,
             notes="Blocked: waiting on the partner API key.")
        make(alpha, "Burndown chart", T.blocked, [adele], order=1,
             notes="Blocked on design sign-off.")

        # Mobile Companion -- in testing.
        make(mobile, "Driver check-in flow", T.testing, [adele], order=0)
        make(mobile, "Offline cache", T.testing, [adele], order=1)
        for i in range(6):
            make(mobile, f"Screen polish {i + 1}", T.done, [adele], order=i, age=15 + i)

        # Billing -- waiting on client, near deadline -> at risk/blocked.
        make(billing, "Invoice templating", T.in_progress, [darryl], Priority.high, date(2026, 6, 22), order=0)
        make(billing, "Tax rules engine", T.blocked, [darryl], Priority.critical, order=0,
             notes="Blocked: need confirmed VAT rules from client finance team.")
        for i in range(3):
            make(billing, f"Usage metering {i + 1}", T.done, [darryl], age=12 + i, order=i)

        # Brand -- completed.
        for i in range(8):
            make(brand, f"Page {i + 1}", T.done, [adele if i % 2 else henri], age=40 + i, order=i)

        # Warehouse Sync -- planning, all todo.
        for i, name in enumerate(["Scanner protocol spike", "Data model", "Sync API", "Conflict resolution"]):
            make(scoping, name, T.todo, [henri if i % 2 else darryl], order=i, age=4)

        db.flush()
        for p in (portal, alpha, mobile, billing, brand, scoping):
            recompute_health(p)
        # Billing is genuinely at risk -- pin it so the dashboard shows a red flag.
        billing.health = ProjectHealth.at_risk

        # -- Comments -------------------------------------------------------
        auth_task = db.scalar(select(Task).where(Task.title == "Authentication"))
        report_task = db.scalar(select(Task).where(Task.title == "Reporting module"))
        db.add_all([
            Comment(task_id=auth_task.id, user_id=henri.id, created_at=_dt(1.2),
                    body="Started wiring up the Entra ID side. Need clarification from the client "
                         "on which tenant we're targeting."),
            Comment(task_id=auth_task.id, user_id=jean.id, created_at=_dt(1.0),
                    body="@henri use the staging tenant for now, I'll confirm prod with the client."),
            Comment(task_id=report_task.id, user_id=darryl.id, created_at=_dt(0.3),
                    body="Fixed the date-range bug in commit af8392. Ready for a look."),
        ])

        # -- Daily progress reports ----------------------------------------
        db.add_all([
            ProgressReport(user_id=henri.id, project_id=portal.id, report_date=date.today(),
                           today="Fixed login bug\nAdded API endpoint\nFinished authentication scaffold",
                           tomorrow="Continue user permissions\nStart notifications",
                           blocked="Waiting for client response on tenant", created_at=_dt(0.2)),
            ProgressReport(user_id=darryl.id, project_id=alpha.id, report_date=date.today(),
                           today="Wired query builder stages 4-6\nReviewed Henri's PR",
                           tomorrow="External data connector once the API key lands",
                           blocked="External partner API key", created_at=_dt(0.25)),
            ProgressReport(user_id=adele.id, project_id=mobile.id, report_date=date.today() - timedelta(days=1),
                           today="Driver check-in flow testing\nOffline cache edge cases",
                           tomorrow="Sign-off pass with QA", blocked="", created_at=_dt(1.1)),
        ])

        # -- Activity feed --------------------------------------------------
        db.add_all([
            Activity(user_id=henri.id, project_id=portal.id, task_id=auth_task.id,
                     verb="completed", summary='completed "Fix Login"', created_at=_dt(0.18)),
            Activity(user_id=darryl.id, project_id=portal.id, task_id=report_task.id,
                     verb="commented", summary='commented on "Reporting module"', created_at=_dt(0.30)),
            Activity(user_id=adele.id, project_id=portal.id,
                     verb="moved", summary='moved "Mobile login screen" to Testing', created_at=_dt(0.55)),
            Activity(user_id=darryl.id, project_id=billing.id,
                     verb="blocked", summary='flagged "Tax rules engine" as Blocked', created_at=_dt(1.4)),
            Activity(user_id=adele.id, project_id=brand.id,
                     verb="completed", summary='completed the final page of Brand Refresh', created_at=_dt(2.1)),
            Activity(user_id=jean.id, project_id=scoping.id,
                     verb="created", summary='created project "Warehouse Sync"', created_at=_dt(6)),
        ])

        db.commit()


if __name__ == "__main__":
    seed()
    print(f"Seeded. Log in with any of the demo emails, password '{DEFAULT_PASSWORD}'.")
    print("Admin: jean@professional.za.com")
