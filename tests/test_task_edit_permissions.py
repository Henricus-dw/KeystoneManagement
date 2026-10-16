import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.db import Base
from app.models import Activity, Priority, Project, Task, TaskStatus, User, UserRole
from app import seed as seed_module
from app.routers.api import (
    EditTaskPayload,
    MovePayload,
    edit_task,
    move_task,
)
from app.routers.pages import task_detail


class TaskEditPermissionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        changelog_db = patch(
            "app.changelog.SessionLocal",
            side_effect=lambda: Session(self.engine),
        )
        changelog_db.start()
        self.addCleanup(changelog_db.stop)

        self.assigner = User(
            name="Assigner", email="assigner@example.test",
            password_hash="unused", role=UserRole.developer,
        )
        self.assignee = User(
            name="Assignee", email="assignee@example.test",
            password_hash="unused", role=UserRole.developer,
        )
        self.other = User(
            name="Other", email="other@example.test",
            password_hash="unused", role=UserRole.admin,
        )
        self.project = Project(name="Project", members=[self.assigner])
        self.task = Task(
            project=self.project, title="Original",
            assignees=[self.assignee],
        )
        self.db.add_all([self.assigner, self.assignee, self.other, self.project, self.task])
        self.db.flush()
        self.task.created_by = self.assigner.id
        self.db.commit()

    def payload(self, **changes):
        values = {
            "title": "Updated",
            "description": "New description",
            "notes": "New notes",
            "status": TaskStatus.in_progress.value,
            "priority": Priority.high.value,
            "due_date": "2026-11-20",
            "assignees": [self.other.id],
        }
        values.update(changes)
        return EditTaskPayload(**values)

    def test_assigner_can_edit_task_fields_and_assignees(self):
        background = BackgroundTasks()
        result = edit_task(
            self.task.id, self.payload(), background,
            user=self.assigner, db=self.db,
        )

        self.assertTrue(result["ok"])
        self.db.refresh(self.task)
        self.assertEqual(self.task.title, "Updated")
        self.assertEqual(self.task.description, "New description")
        self.assertEqual(self.task.notes, "New notes")
        self.assertEqual(self.task.status, TaskStatus.in_progress)
        self.assertEqual(self.task.priority, Priority.high)
        self.assertEqual([assignee.id for assignee in self.task.assignees], [self.other.id])
        self.assertEqual(self.task.due_date.isoformat(), "2026-11-20")
        activity = self.db.scalar(select(Activity).where(Activity.task_id == self.task.id))
        self.assertEqual(activity.verb, "moved")
        self.assertEqual(len(background.tasks), 1)
        self.assertEqual(background.tasks[0].kwargs["recipient"], self.other.email)

    def test_non_assigner_cannot_edit_or_move_task(self):
        result = edit_task(
            self.task.id, self.payload(), BackgroundTasks(),
            user=self.other, db=self.db,
        )

        self.assertFalse(result["ok"])
        self.db.refresh(self.task)
        self.assertEqual(self.task.title, "Original")
        self.assertEqual(self.task.status, TaskStatus.todo)
        moved = move_task(
            self.task.id, MovePayload(status=TaskStatus.done.value),
            user=self.other, db=self.db,
        )
        self.assertFalse(moved["ok"])
        self.db.refresh(self.task)
        self.assertEqual(self.task.status, TaskStatus.todo)

    def test_invalid_assignee_and_empty_title_are_rejected_without_changes(self):
        invalid_assignee = edit_task(
            self.task.id, self.payload(assignees=[99999]),
            BackgroundTasks(),
            user=self.assigner, db=self.db,
        )
        empty_title = edit_task(
            self.task.id, self.payload(title="   "),
            BackgroundTasks(),
            user=self.assigner, db=self.db,
        )

        self.assertFalse(invalid_assignee["ok"])
        self.assertFalse(empty_title["ok"])
        self.db.refresh(self.task)
        self.assertEqual(self.task.title, "Original")
        self.assertEqual(self.task.status, TaskStatus.todo)

    def test_task_detail_only_renders_editor_for_assigner(self):
        def request():
            return Request({
                "type": "http", "method": "GET",
                "path": f"/tasks/{self.task.id}",
                "query_string": b"", "headers": [],
            })

        owner_page = task_detail(
            self.task.id, request(), user=self.assigner, db=self.db,
        )
        assignee_page = task_detail(
            self.task.id, request(), user=self.assignee, db=self.db,
        )

        self.assertIn(b'id="taskEditForm"', owner_page.body)
        self.assertNotIn(b'id="taskEditForm"', assignee_page.body)


class TaskCreatorMigrationTests(unittest.TestCase):
    def test_migrate_backfills_linked_and_legacy_creation_activities(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql("INSERT INTO users (id) VALUES (7)")
            conn.exec_driver_sql(
                "CREATE TABLE projects (id INTEGER PRIMARY KEY, created_by INTEGER)"
            )
            conn.exec_driver_sql("INSERT INTO projects (id, created_by) VALUES (2, 7)")
            conn.exec_driver_sql("""
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY, project_id INTEGER, title TEXT,
                    created_at TEXT
                )
            """)
            conn.exec_driver_sql("""
                CREATE TABLE activities (
                    id INTEGER PRIMARY KEY, user_id INTEGER, project_id INTEGER,
                    task_id INTEGER, verb TEXT, summary TEXT, created_at TEXT
                )
            """)
            conn.exec_driver_sql(
                "INSERT INTO tasks VALUES (11, 2, 'Linked task', '2026-01-01 00:00:00')"
            )
            conn.exec_driver_sql(
                "INSERT INTO tasks VALUES (12, 2, 'Legacy task', '2026-01-02 00:00:00')"
            )
            conn.exec_driver_sql("""
                INSERT INTO activities
                (id, user_id, project_id, task_id, verb, summary, created_at)
                VALUES (21, 7, 2, 11, 'created', 'created task "Linked task"',
                        '2026-01-01 00:00:00')
            """)
            conn.exec_driver_sql("""
                INSERT INTO activities
                (id, user_id, project_id, task_id, verb, summary, created_at)
                VALUES (22, 7, 2, NULL, 'created', 'created task "Legacy task"',
                        '2026-01-02 00:00:00')
            """)

        try:
            with patch.object(seed_module, "engine", engine):
                seed_module._migrate()
            with engine.connect() as conn:
                creators = conn.exec_driver_sql(
                    "SELECT id, created_by FROM tasks ORDER BY id"
                ).all()
            self.assertEqual(creators, [(11, 7), (12, 7)])
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
