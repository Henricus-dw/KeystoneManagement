import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from fastapi import BackgroundTasks
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.db import Base
from app.models import Project, Task, User, UserRole
from app.notifications import send_task_assignment_email
from app.routers.api import CreatePayload, create_task
from app.routers.pages import board, project_detail, task_detail


class AssigneeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.checkboxes = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "checkbox":
            self.checkboxes[int(attrs["value"])] = "checked" in attrs


class TaskAssigneeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        changelog_db = patch("app.changelog.SessionLocal",
                             side_effect=lambda: Session(self.engine))
        changelog_db.start()
        self.addCleanup(changelog_db.stop)
        self.creator = User(
            name="Creator", email="creator@example.test",
            password_hash="unused", role=UserRole.developer,
        )
        self.admin = User(
            name="Admin", email="admin@example.test",
            password_hash="unused", role=UserRole.admin,
        )
        self.manager = User(
            name="Manager", email="manager@example.test",
            password_hash="unused", role=UserRole.manager,
        )
        self.developer = User(
            name="Outside developer", email="developer@example.test",
            password_hash="unused", role=UserRole.developer,
        )
        self.project = Project(name="Project", members=[self.creator])
        self.db.add_all([self.project, self.admin, self.manager, self.developer])
        self.db.commit()

    def request(self, path):
        return Request({"type": "http", "method": "GET", "path": path,
                        "query_string": b"", "headers": []})

    def create(self, assignees):
        background = BackgroundTasks()
        result = create_task(
            CreatePayload(project_id=self.project.id, title="Assigned task",
                          assignees=assignees),
            background, user=self.creator, db=self.db,
        )
        return result, background

    def test_both_creation_dialogs_list_every_role_and_nonmembers(self):
        responses = [
            board(self.request("/board"), project_id=self.project.id,
                  user=self.creator, db=self.db),
            project_detail(self.project.id, self.request("/projects/1"),
                           user=self.creator, db=self.db),
        ]
        expected = {
            self.creator.id: True, self.admin.id: False,
            self.manager.id: False, self.developer.id: False,
        }
        for response in responses:
            with self.subTest(template=response.template.name):
                parser = AssigneeParser()
                parser.feed(response.body.decode())
                self.assertEqual(parser.checkboxes, expected)
                self.assertEqual(response.body.decode().count("Select anyone on the team."), 1)
                self.assertEqual(
                    [u.name for u in response.context["assignee_opts"]],
                    ["Admin", "Creator", "Manager", "Outside developer"],
                )

    def test_single_outside_assignee_is_saved_and_notified(self):
        result, background = self.create([self.manager.id])
        self.assertTrue(result["ok"])
        task_id = result["task"]["id"]
        self.db.expire_all()
        task = self.db.get(Task, task_id)
        self.assertEqual([u.id for u in task.assignees], [self.manager.id])
        self.assertEqual(result["task"]["assignees"][0]["name"], self.manager.name)
        self.assertEqual(len(background.tasks), 1)
        self.assertIs(background.tasks[0].func, send_task_assignment_email)
        self.assertEqual(background.tasks[0].kwargs["recipient"], self.manager.email)
        self.assertEqual(background.tasks[0].kwargs["task_id"], task_id)
        response = task_detail(task_id, self.request(f"/tasks/{task_id}"),
                               user=self.creator, db=self.db)
        self.assertIn(f'title="{self.manager.name}"', response.body.decode())
        response = board(self.request("/board"), project_id=self.project.id,
                         user=self.creator, db=self.db)
        self.assertIn(f'title="{self.manager.name}"', response.body.decode())

    def test_multiple_assignees_are_saved_and_notified_once(self):
        ids = [self.admin.id, self.manager.id, self.developer.id]
        result, background = self.create(ids + [self.admin.id])
        self.assertTrue(result["ok"])
        task_id = result["task"]["id"]
        self.db.expire_all()
        task = self.db.get(Task, task_id)
        self.assertEqual({u.id for u in task.assignees}, set(ids))
        self.assertEqual(len(background.tasks), 3)
        self.assertEqual({t.kwargs["recipient"] for t in background.tasks},
                         {self.admin.email, self.manager.email, self.developer.email})

    def test_no_selection_preserves_creator_default(self):
        result, background = self.create([])
        self.assertTrue(result["ok"])
        task = self.db.get(Task, result["task"]["id"])
        self.assertEqual([u.id for u in task.assignees], [self.creator.id])
        self.assertEqual(background.tasks[0].kwargs["recipient"], self.creator.email)

    def test_invalid_assignees_reject_creation_without_notifications(self):
        for ids in ([99999], [self.manager.id, 99999]):
            with self.subTest(assignees=ids):
                result, background = self.create(ids)
                self.assertFalse(result["ok"])
                self.assertIn("assignees", result["error"])
                self.assertEqual(self.db.scalar(select(func.count(Task.id))), 0)
                self.assertEqual(background.tasks, [])


if __name__ == "__main__":
    unittest.main()
