import asyncio
import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner
from sqlalchemy import Column, MetaData, Table, create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.access import GUEST_PAGES, can_access_page, landing_page
from app.db import Base, get_db
from app.deps import AuthRedirect
from app.models import Activity, MonthlyHoursCycle, Project, Server, Task, User, UserRole
from app.routers import api, auth, backups, changelog, pages, servers
from app.security import hash_password, verify_password
from app.seed import _migrate
from app.templating import templates


class GuestAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.admin = User(name="Admin", email="admin@example.test",
                          password_hash=hash_password("test-password"), role=UserRole.admin)
        self.guest = User(name="Guest", email="guest@example.test",
                          password_hash=self.admin.password_hash, role=UserRole.guest)
        self.developer = User(name="Developer", email="dev@example.test",
                              password_hash=self.admin.password_hash, role=UserRole.developer)
        self.manager = User(name="Manager", email="manager@example.test",
                            password_hash=self.admin.password_hash, role=UserRole.manager)
        self.db.add_all([self.admin, self.guest, self.developer, self.manager])
        self.db.flush()
        self.project = Project(name="Example", members=[self.developer, self.guest])
        self.task = Task(title="Example task", project=self.project,
                         created_by=self.guest.id, assignees=[self.guest])
        self.server = Server(name="Shared server", owner_id=self.developer.id, shared=True)
        self.private_server = Server(name="Private server", owner_id=self.developer.id, shared=False)
        self.db.add_all([self.project, self.task, self.server, self.private_server])
        self.db.commit()
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="guest-test-secret")
        for router in (auth.router, pages.router, api.router, backups.router, servers.router, changelog.router):
            self.app.include_router(router)

        @self.app.exception_handler(AuthRedirect)
        async def redirect(request: Request, exc: AuthRedirect):
            return RedirectResponse(exc.location, status_code=303)

        def test_db():
            yield self.db

        self.app.dependency_overrides[get_db] = test_db
        globals_patch = patch.dict(templates.env.globals, {"app_version": lambda: None})
        globals_patch.start()
        self.addCleanup(globals_patch.stop)

    def http(self, url, method="GET", user=None, form=None, payload=None, headers=None):
        target = urlsplit(url)
        request_headers = [(b"host", b"testserver")]
        if user is not None:
            session = base64.b64encode(json.dumps({"user_id": user.id}).encode())
            cookie = TimestampSigner("guest-test-secret").sign(session)
            request_headers.append((b"cookie", b"session=" + cookie))
        body = b""
        if form is not None:
            body = urlencode(form, doseq=True).encode()
            request_headers.append((b"content-type", b"application/x-www-form-urlencoded"))
        if payload is not None:
            body = json.dumps(payload).encode()
            request_headers.append((b"content-type", b"application/json"))
        request_headers.extend((key.encode(), value.encode()) for key, value in (headers or {}).items())
        messages = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        async def run():
            await self.app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "https", "path": target.path,
                "raw_path": target.path.encode(), "query_string": target.query.encode(),
                "headers": request_headers, "client": ("127.0.0.1", 1234),
                "server": ("testserver", 443),
            }, receive, send)

        asyncio.run(run())
        start = next(m for m in messages if m["type"] == "http.response.start")
        response_body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return start["status"], dict(start["headers"]), response_body

    def grant(self, *keys):
        self.guest.guest_pages = list(keys)
        self.db.commit()

    def user_form(self, **changes):
        values = {
            "name": "New Guest", "email": "new-guest@example.test",
            "password": "temporary-password", "role": "Guest", "title": "",
        }
        values.update(changes)
        return values

    def test_guest_defaults_to_no_pages_and_safe_landing(self):
        self.assertEqual(self.guest.guest_pages, [])
        self.assertEqual(landing_page(self.guest), "/account")
        for _, path in GUEST_PAGES.values():
            with self.subTest(path=path):
                self.assertEqual(self.http(path, user=self.guest)[0], 403)
        for path in ("/", "/login"):
            status, headers, _ = self.http(path, user=self.guest)
            self.assertEqual(status, 303)
            self.assertEqual(headers[b"location"], b"/account")
        body = self.http("/account", user=self.guest)[2].decode()
        self.assertIn("No pages have been granted", body)
        self.assertNotIn('href="/dashboard"', body)
        self.assertNotIn('href="/changelog"', body)
        self.assertIn('href="/logout"', body)

    def test_every_selected_page_is_readable_and_does_not_grant_other_pages(self):
        for key, (_, path) in GUEST_PAGES.items():
            self.grant(key)
            with self.subTest(key=key):
                self.assertEqual(self.http(path, user=self.guest)[0], 200)
                self.assertEqual(landing_page(self.guest), path)
                self.assertEqual(self.http("/admin/users", user=self.guest)[0], 403)
                for other_key, (_, other_path) in GUEST_PAGES.items():
                    if key != other_key:
                        self.assertEqual(self.http(other_path, user=self.guest)[0], 403)

    def test_backup_json_only_contains_granted_platforms(self):
        for key, expected, target in (
            ("backups-learning", ["e-learning", "moodle"], "/backups/learning"),
            ("backups-apps", ["insight", "asimolog"], "/backups/apps"),
        ):
            self.grant(key)
            status, _, body = self.http("/api/backups", user=self.guest)
            self.assertEqual(status, 200)
            snapshot = json.loads(body)
            self.assertEqual([p["key"] for p in snapshot["platforms"]], expected)
            self.assertEqual(sum(snapshot["counts"].values()), 2)
            self.assertEqual(self.http("/backups", user=self.guest)[1][b"location"].decode(), target)
            html = self.http(target, user=self.guest)[2].decode()
            other = "/backups/apps" if key == "backups-learning" else "/backups/learning"
            self.assertNotIn(f'href="{other}"', html)
        self.grant()
        self.assertEqual(self.http("/api/backups", user=self.guest)[0], 403)
        self.assertEqual(self.http("/backups", user=self.guest)[0], 403)

    def test_all_staff_mutation_routes_are_blocked_for_guests(self):
        self.grant(*GUEST_PAGES)
        for route in self.app.routes:
            if not getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}:
                continue
            if route.path == "/login" or route.path.startswith(("/account/", "/api/backups/")):
                continue
            path = route.path
            for name in ("project_id", "task_id", "server_id", "release_id"):
                path = path.replace("{" + name + "}", "1")
            path = path.replace("{user_id}", str(self.guest.id))
            for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.http(path, method, user=self.guest, form={})[0], 403)
        self.assertEqual(self.db.get(Task, self.task.id).title, "Example task")
        self.assertEqual(self.db.scalar(select(func.count(Activity.id))), 0)
        self.assertEqual(self.guest.role, UserRole.guest)

    def test_read_only_forms_and_model_permissions_even_when_guest_is_owner(self):
        self.grant(*GUEST_PAGES)
        self.assertFalse(self.task.can_edit(self.guest))
        for path, absent in (
            ("/projects", ["/projects/new"]),
            ("/servers", ["/servers/new"]),
            (f"/tasks/{self.task.id}", ['method="post"', 'id="taskEditForm"']),
            (f"/projects/{self.project.id}", ['method="post"', 'id="projectAddTask"']),
            (f"/servers/{self.server.id}", ['method="post"', "Delete server", ">Edit</a>"]),
            ("/hours-tracker", ['method="post"', "Submit my hours"]),
            ("/board", ['draggable="true"', 'data-editable="true"', "Add task"]),
        ):
            status, _, body = self.http(path, user=self.guest)
            self.assertEqual(status, 200)
            for text in absent:
                with self.subTest(path=path, text=text):
                    self.assertNotIn(text, body.decode())
        owned = Server(name="Owned", owner_id=self.guest.id)
        self.assertFalse(owned.can_manage(self.guest))
        self.assertFalse(owned.can_share(self.guest))
        self.assertEqual(self.http("/projects/new", user=self.guest)[0], 403)
        self.assertEqual(self.http(f"/projects/{self.project.id}/edit", user=self.guest)[0], 403)
        self.assertEqual(self.http("/servers/new", user=self.guest)[0], 403)
        self.assertEqual(self.http(f"/servers/{self.server.id}/edit", user=self.guest)[0], 403)
        self.assertNotEqual(self.http(f"/servers/{self.private_server.id}", user=self.guest)[0], 200)

    def test_detail_and_file_routes_require_their_page_grant(self):
        paths = (f"/projects/{self.project.id}", f"/projects/{self.project.id}/attachments/1",
                 f"/tasks/{self.task.id}", f"/tasks/{self.task.id}/attachments/1",
                 "/tasks/recent", f"/servers/{self.server.id}", f"/servers/{self.server.id}/image",
                 "/hours-tracker/review", "/activity/export.pdf")
        for path in paths:
            self.assertEqual(self.http(path, user=self.guest)[0], 403, path)
        self.grant("projects")
        self.assertEqual(self.http(f"/tasks/{self.task.id}", user=self.guest)[0], 200)
        self.assertEqual(self.http("/board", user=self.guest)[0], 403)
        self.grant("board")
        self.assertEqual(self.http(f"/tasks/{self.task.id}", user=self.guest)[0], 200)
        self.assertEqual(self.http(f"/projects/{self.project.id}", user=self.guest)[0], 403)

    def test_hours_get_does_not_create_or_modify_workbooks_for_guests(self):
        self.grant("hours")
        with patch("app.routers.pages._ensure_hours_workbook") as ensure:
            for path in ("/hours-tracker", "/hours-tracker/review"):
                self.assertEqual(self.http(path, user=self.guest)[0], 200)
            ensure.assert_not_called()
        self.assertEqual(self.db.scalar(select(func.count(MonthlyHoursCycle.report_month))), 0)
        html = templates.get_template("hours_review.html").render(
            request=Request({"type": "http", "headers": []}), user=self.guest, nav="hours",
            month="2026-10", month_label="October 2026", sent=False, waiting=False,
            all_members_submitted=True,
            sheets=[{"title": "Member", "rows": [["Customer", "Hours"], ["Example", 8]]}],
        )
        self.assertIn("Example", html)
        for text in ('contenteditable="true"', 'method="post"', "fetch(", "data-delete-row"):
            self.assertNotIn(text, html)

    def test_admin_can_create_update_revoke_and_convert_guests(self):
        form = self.user_form(guest_pages=["backups-apps", "servers", "backups-apps"])
        self.assertEqual(self.http("/admin/users", "POST", user=self.admin, form=form)[0], 303)
        target = self.db.scalar(select(User).where(User.email == form["email"]))
        self.assertEqual(target.role, UserRole.guest)
        self.assertEqual(target.guest_pages, ["backups-apps", "servers"])
        self.assertEqual(target.title, "Guest")
        self.assertTrue(verify_password(form["password"], target.password_hash))
        self.assertEqual(self.http("/backups/apps", user=target)[0], 200)
        edit = {**form, "password": "", "guest_pages": ["team"]}
        self.assertEqual(self.http(f"/admin/users/{target.id}", "POST", user=self.admin, form=edit)[0], 303)
        self.db.refresh(target)
        self.assertEqual(target.guest_pages, ["team"])
        self.assertEqual(self.http("/backups/apps", user=target)[0], 403)
        self.assertEqual(self.http("/team", user=target)[0], 200)
        edit.pop("guest_pages")
        self.http(f"/admin/users/{target.id}", "POST", user=self.admin, form=edit)
        self.db.refresh(target)
        self.assertEqual(target.guest_pages, [])
        self.assertEqual(self.http("/team", user=target)[0], 403)
        self.http(f"/admin/users/{target.id}", "POST", user=self.admin,
                  form={**edit, "role": "Developer", "guest_pages": ["servers"]})
        self.db.refresh(target)
        self.assertEqual(target.role, UserRole.developer)
        self.assertEqual(target.guest_pages, [])
        self.http(f"/admin/users/{target.id}", "POST", user=self.admin, form=edit)
        self.db.refresh(target)
        self.assertEqual(target.role, UserRole.guest)
        self.assertEqual(target.guest_pages, [])
        with Session(self.engine) as db:
            persisted = db.get(User, target.id)
            self.assertEqual(persisted.role, UserRole.guest)
            self.assertEqual(persisted.guest_pages, [])

    def test_non_admins_cannot_manage_guests_or_self_grant(self):
        for user in (self.guest, self.developer, self.manager):
            for path in ("/admin/users", f"/admin/users/{self.guest.id}"):
                status = self.http(path, "POST", user=user,
                                   form=self.user_form(guest_pages=["dashboard"]))[0]
                self.assertIn(status, (303, 403))
        self.assertEqual(self.guest.guest_pages, [])
        self.assertEqual(self.db.scalar(select(func.count(User.id))), 4)
        self.http("/account/color", "POST", user=self.guest,
                  form={"accent": "#123456", "guest_pages": ["dashboard"], "role": "Admin"})
        self.assertEqual(self.guest.role, UserRole.guest)
        self.assertEqual(self.guest.guest_pages, [])

    def test_invalid_page_grants_and_roles_are_rejected_without_writes(self):
        for change in ({"guest_pages": ["admin"]}, {"guest_pages": ["unknown"]}, {"role": "Superuser"}):
            with self.subTest(change=change):
                form = self.user_form(**change)
                self.assertEqual(self.http("/admin/users", "POST", user=self.admin, form=form)[0], 422)
                self.assertEqual(self.http(f"/admin/users/{self.guest.id}", "POST",
                                           user=self.admin, form=form)[0], 422)
        self.assertEqual(self.db.scalar(select(func.count(User.id))), 4)
        self.assertEqual(self.guest.email, "guest@example.test")
        self.assertEqual(self.guest.guest_pages, [])

    def test_guest_login_redirects_to_first_granted_page_or_account(self):
        for grant, expected in (([], "/account"), (["backups-apps"], "/backups/apps"),
                                (["servers", "dashboard"], "/dashboard")):
            self.grant(*grant)
            form = {"email": self.guest.email, "password": "test-password"}
            status, headers, _ = self.http("/login", "POST", form=form)
            self.assertEqual(status, 303)
            self.assertEqual(headers[b"location"].decode(), expected + "?welcome=1")
            status, _, body = self.http("/login", "POST", form=form, headers={"x-requested-with": "fetch"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["next"], expected)

    def test_guests_can_change_own_password_and_colour_and_logout(self):
        status, _, _ = self.http("/account/password", "POST", user=self.guest,
                                 form={"current": "test-password", "new": "new-password", "confirm": "new-password"})
        self.assertEqual(status, 303)
        self.assertTrue(verify_password("new-password", self.guest.password_hash))
        self.assertEqual(self.http("/account/color", "POST", user=self.guest, form={"accent": "#123456"})[0], 303)
        self.assertEqual(self.guest.accent, "#123456")
        self.assertEqual(self.http("/logout", user=self.guest)[1][b"location"], b"/login")

    def test_navigation_and_admin_checkboxes_reflect_saved_permissions(self):
        self.grant("servers", "backups-apps")
        html = self.http("/account", user=self.guest)[2].decode()
        for key, (_, path) in GUEST_PAGES.items():
            if key in self.guest.guest_pages:
                self.assertIn(f'href="{path}"', html)
            else:
                self.assertNotIn(f'href="{path}"', html)
        self.assertNotIn('href="/admin/users"', html)
        html = self.http("/admin/users", user=self.admin)[2].decode()
        self.assertIn('<option value="Guest"', html)
        self.assertIn('name="guest_pages" value="servers" checked', html)
        self.assertIn("Guest page access", html)
        self.assertIn("read-only", html)

    def test_staff_access_and_oversight_restrictions_remain_unchanged(self):
        for user in (self.admin, self.manager, self.developer):
            self.assertEqual(landing_page(user), "/dashboard")
            for key, (_, path) in GUEST_PAGES.items():
                status = self.http(path, user=user)[0]
                if key in ("reports", "activity") and user.role == UserRole.developer:
                    self.assertEqual(status, 303)
                    self.assertFalse(can_access_page(user, key))
                else:
                    self.assertEqual(status, 200, (user.role, path))
        self.assertEqual(self.http("/dashboard")[0], 303)
        self.assertEqual(self.http("/backups/apps")[0], 303)

    def test_legacy_user_schema_migrates_idempotently_and_preserves_staff(self):
        with tempfile.TemporaryDirectory(prefix="keystone-guest-migration-") as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'legacy.db'}")
            try:
                Base.metadata.create_all(engine)
                User.__table__.drop(engine)
                metadata = MetaData()
                old = Table("users", metadata, *[
                    Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
                    for c in User.__table__.columns if c.name != "guest_pages"
                ])
                metadata.create_all(engine)
                with engine.begin() as conn:
                    conn.execute(old.insert().values(
                        id=1, name="Existing", email="existing@example.test",
                        password_hash="unchanged", role=UserRole.developer, title="Developer",
                        accent="#1CC4D8", created_at=datetime.now(timezone.utc)))
                with patch("app.seed.engine", engine):
                    _migrate()
                    _migrate()
                with Session(engine) as db:
                    existing = db.get(User, 1)
                    self.assertEqual(existing.guest_pages, [])
                    self.assertEqual(existing.role, UserRole.developer)
                    self.assertEqual(existing.password_hash, "unchanged")
                    db.add(User(name="Guest", email="migrated-guest@example.test", password_hash="unused",
                                role=UserRole.guest, guest_pages=["backups-apps"]))
                    db.commit()
                engine.dispose()
                with Session(engine) as db:
                    guest = db.scalar(select(User).where(User.role == UserRole.guest))
                    self.assertEqual(guest.guest_pages, ["backups-apps"])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
