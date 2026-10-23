import asyncio
import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.backups import HISTORY_LIMIT, PLATFORMS, backup_overview, backup_size, backup_time
from app.db import Base, get_db
from app.deps import AuthRedirect
from app.models import BackupCheck, BackupRun, User, UserRole
from app.routers.backups import BackupReport, IntegrityReport, _stored_values, _update_run, router
from app.templating import templates


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(name="Viewer", email="viewer@example.test",
                         password_hash="unused", role=UserRole.developer)
        self.db.add(self.user)
        self.db.commit()
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="backup-test-secret")
        self.app.include_router(router)

        @self.app.exception_handler(AuthRedirect)
        async def redirect(request: Request, exc: AuthRedirect):
            return RedirectResponse(exc.location, status_code=303)

        def test_db():
            yield self.db

        self.app.dependency_overrides[get_db] = test_db
        self.tokens = {key: f"{key}-" + "x" * 40 for key in PLATFORMS}
        env = patch.dict("os.environ", {
            f"KEYSTONE_BACKUP_TOKEN_{key.upper().replace('-', '_')}": token
            for key, token in self.tokens.items()
        })
        env.start()
        self.addCleanup(env.stop)
        globals_patch = patch.dict(templates.env.globals, {"app_version": lambda: None})
        globals_patch.start()
        self.addCleanup(globals_patch.stop)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.payload = {
            "run_id": "run-1", "status": "successful",
            "started_at": (self.now - timedelta(hours=1)).isoformat(),
            "completed_at": self.now.isoformat(),
            "next_scheduled_at": (self.now + timedelta(days=1)).isoformat(),
            "overdue_at": (self.now + timedelta(days=1, hours=2)).isoformat(),
            "destination": "AsimoLog physical server",
            "processed_bytes": 0, "repository_bytes": 1024,
        }

    def http(self, path, method="GET", payload=None, token=None, signed_in=False):
        headers = [(b"host", b"testserver")]
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        if signed_in:
            session = base64.b64encode(json.dumps({"user_id": self.user.id}).encode())
            cookie = TimestampSigner("backup-test-secret").sign(session)
            headers.append((b"cookie", b"session=" + cookie))
        body = json.dumps(payload).encode() if payload is not None else b""
        if payload is not None:
            headers.append((b"content-type", b"application/json"))
        messages = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        async def run():
            await self.app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "https", "path": path,
                "raw_path": path.encode(), "query_string": b"", "headers": headers,
                "client": ("127.0.0.1", 1234), "server": ("testserver", 443),
            }, receive, send)

        asyncio.run(run())
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_body = b"".join(message.get("body", b"") for message in messages
                                 if message["type"] == "http.response.body")
        return start["status"], dict(start["headers"]), response_body

    def report(self, payload=None, platform="e-learning"):
        return self.http(f"/api/backups/{platform}/reports", "POST",
                         payload if payload is not None else self.payload,
                         token=self.tokens[platform])

    def overview(self, now=None):
        return backup_overview(self.db, now or self.now)

    def test_unknown_is_not_success_and_all_four_platforms_exist(self):
        overview = self.overview()
        self.assertEqual(overview["counts"]["unknown"], 4)
        self.assertEqual(overview["counts"]["successful"], 0)
        self.assertEqual([p["key"] for p in overview["platforms"]], list(PLATFORMS))
        self.assertTrue(all(p["latest"] is None for p in overview["platforms"]))

    def test_both_monitoring_routes_require_login(self):
        for path in ("/backups", "/api/backups"):
            status, headers, _ = self.http(path)
            self.assertEqual(status, 303)
            self.assertEqual(headers[b"location"], b"/login")

    def test_all_signed_in_users_can_read_page_and_api(self):
        for role in UserRole:
            self.user.role = role
            self.db.commit()
            for path in ("/backups", "/api/backups"):
                status, headers, body = self.http(path, signed_in=True)
                self.assertEqual(status, 200)
                self.assertEqual(headers[b"cache-control"], b"no-store")
                self.assertIn(b"E-Learning", body)
                self.assertNotIn(self.tokens["e-learning"].encode(), body)

    def test_tokens_are_required_and_platform_scoped(self):
        for token in (None, "wrong", self.tokens["moodle"], "\u00e9" * 40):
            status, _, _ = self.http("/api/backups/e-learning/reports", "POST",
                                     self.payload, token=token, signed_in=True)
            self.assertEqual(status, 401)
        self.assertEqual(self.db.scalar(select(func.count(BackupRun.id))), 0)
        self.assertEqual(self.http("/api/backups/other/reports", "POST", self.payload,
                                   token=self.tokens["moodle"])[0], 404)

    def test_missing_short_and_reused_configuration_is_explicit(self):
        for value in ("", "short", self.tokens["moodle"]):
            with patch.dict("os.environ", {"KEYSTONE_BACKUP_TOKEN_E_LEARNING": value}):
                self.assertEqual(self.report()[0], 503)

    def test_success_persists_and_retries_are_idempotent(self):
        self.assertEqual(self.report()[0], 200)
        self.assertEqual(self.report()[0], 200)
        self.db.expire_all()
        self.assertEqual(self.db.scalar(select(func.count(BackupRun.id))), 1)
        platform = self.overview()["platforms"][0]
        self.assertEqual(platform["status"], "successful")
        self.assertEqual(platform["latest"]["processed_bytes"], 0)
        self.assertEqual(platform["latest"]["repository_bytes"], 1024)
        self.assertEqual(platform["last_successful_at"], self.now.isoformat())
        self.assertEqual(platform["integrity_history"], [])

    def test_records_survive_database_reopen_and_existing_schema_initialization(self):
        with tempfile.TemporaryDirectory(prefix="keystone-backup-test-") as directory:
            path = Path(directory) / "monitoring.db"
            engine = create_engine(f"sqlite:///{path}")
            try:
                # Simulate an existing deployment before backup tables existed.
                User.__table__.create(engine)
                Base.metadata.create_all(engine)
                with Session(engine) as db:
                    db.add(BackupRun(platform="e-learning", **_stored_values(
                        BackupReport(**self.payload),
                    )))
                    db.commit()
            finally:
                engine.dispose()
            reopened = create_engine(f"sqlite:///{path}")
            try:
                Base.metadata.create_all(reopened)
                with Session(reopened) as db:
                    self.assertEqual(backup_overview(db, self.now)["platforms"][0]["status"],
                                     "successful")
                    self.assertEqual(db.scalar(select(func.count(BackupRun.id))), 1)
            finally:
                reopened.dispose()

    def test_running_can_finish_but_final_result_cannot_be_rewritten(self):
        running = {**self.payload, "status": "running", "completed_at": None,
                   "expected_completion_at": (self.now + timedelta(hours=1)).isoformat()}
        self.assertEqual(self.report(running)[0], 200)
        self.assertEqual(self.overview()["platforms"][0]["status"], "running")
        self.assertEqual(self.report()[0], 200)
        self.assertEqual(self.report(running)[0], 409)
        self.assertEqual(self.report({**self.payload, "status": "failed", "error": "Disk full"})[0], 409)
        self.assertEqual(self.overview()["platforms"][0]["status"], "successful")

    def test_run_id_start_time_is_immutable(self):
        running = {**self.payload, "status": "running", "completed_at": None,
                   "expected_completion_at": (self.now + timedelta(hours=1)).isoformat()}
        self.assertEqual(self.report(running)[0], 200)
        changed = {**self.payload, "started_at": (self.now - timedelta(hours=2)).isoformat()}
        self.assertEqual(self.report(changed)[0], 409)

    def test_stale_concurrent_report_cannot_overwrite_a_final_result(self):
        running = {**self.payload, "status": "running", "completed_at": None,
                   "expected_completion_at": (self.now + timedelta(hours=1)).isoformat()}
        self.report(running)
        with Session(self.engine) as stale_db:
            stale_run = stale_db.scalar(select(BackupRun))
            self.assertEqual(stale_run.status, "running")
            self.assertEqual(self.report()[0], 200)
            failed = BackupReport(**{**self.payload, "status": "failed", "error": "Late failure"})
            with self.assertRaises(HTTPException) as raised:
                _update_run(stale_db, stale_run, _stored_values(failed))
            self.assertEqual(raised.exception.status_code, 409)
            stale_db.rollback()
        self.db.expire_all()
        self.assertEqual(self.overview()["platforms"][0]["status"], "successful")

    def test_missed_backup_deadline_exact_boundary(self):
        self.report()
        deadline = datetime.fromisoformat(self.payload["overdue_at"])
        self.assertEqual(self.overview(deadline)["platforms"][0]["status"], "successful")
        platform = self.overview(deadline + timedelta(microseconds=1))["platforms"][0]
        self.assertEqual(platform["status"], "overdue")
        self.assertEqual(platform["latest"]["status"], "successful")

    def test_stalled_running_uses_its_completion_deadline(self):
        deadline = self.now + timedelta(hours=1)
        running = {**self.payload, "status": "running", "completed_at": None,
                   "expected_completion_at": deadline.isoformat()}
        self.report(running)
        self.assertEqual(self.overview(deadline)["platforms"][0]["status"], "running")
        self.assertEqual(self.overview(deadline + timedelta(seconds=1))["platforms"][0]["status"], "overdue")

    def test_delayed_report_never_replaces_newer_run(self):
        self.report()
        older = {**self.payload, "run_id": "older-failure", "status": "failed",
                 "started_at": (self.now - timedelta(days=2)).isoformat(),
                 "completed_at": (self.now - timedelta(days=2) + timedelta(minutes=10)).isoformat(),
                 "error": "Old failure"}
        self.assertEqual(self.report(older)[0], 200)
        self.assertEqual(self.overview()["platforms"][0]["status"], "successful")
        newer = {**self.payload, "run_id": "new-failure", "status": "failed",
                 "started_at": self.now.isoformat(), "error": "<script>alert('x')</script>"}
        self.assertEqual(self.report(newer)[0], 200)
        platform = self.overview()["platforms"][0]
        self.assertEqual(platform["status"], "failed")
        self.assertEqual(platform["last_successful_at"], self.now.isoformat())
        self.assertEqual(self.overview(self.now + timedelta(days=10))["platforms"][0]["status"], "failed")
        body = self.http("/backups", signed_in=True)[2].decode()
        self.assertNotIn("<script>alert", body)
        self.assertIn("&lt;script&gt;", body)

    def test_validation_rejects_inconsistent_or_sensitive_extra_fields(self):
        invalid = [
            {"started_at": "2026-01-01T12:00:00"},
            {"started_at": (self.now + timedelta(days=1)).isoformat()},
            {"completed_at": None}, {"completed_at": "2020-01-01T00:00:00Z"},
            {"completed_at": (self.now + timedelta(days=1)).isoformat()},
            {"status": "unknown"}, {"status": "failed", "error": ""},
            {"status": "running", "completed_at": None},
            {"status": "running", "expected_completion_at": self.now.isoformat()},
            {"next_scheduled_at": self.payload["started_at"]},
            {"overdue_at": self.now.isoformat()},
            {"processed_bytes": -1}, {"repository_bytes": 2**63},
            {"processed_bytes": 1.2}, {"processed_bytes": True},
            {"error": "x" * 4001}, {"error": "Not a successful run"},
            {"run_id": ""}, {"run_id": "bad id"}, {"destination": "   "},
            {"restic_password": "must-not-be-accepted"},
        ]
        for change in invalid:
            with self.subTest(change=change):
                self.assertEqual(self.report({**self.payload, **change})[0], 422)
        self.assertEqual(self.db.scalar(select(func.count(BackupRun.id))), 0)

    def test_timezone_normalization_and_sast_formatting(self):
        offset = timezone(timedelta(hours=2))
        changed = {key: datetime.fromisoformat(value).astimezone(offset).isoformat()
                   if key.endswith("_at") else value for key, value in self.payload.items()}
        self.assertEqual(self.report(changed)[0], 200)
        self.assertEqual(self.report()[0], 200)
        self.assertEqual(backup_time("2026-01-01T23:30:00+00:00"), "02 Jan 2026 01:30:00")
        self.assertEqual(backup_size(0), "0.0 B")
        self.assertEqual(backup_size(1024**3), "1.0 GiB")
        self.assertEqual(backup_size(None), "Not reported")

    def test_integrity_is_independent_idempotent_and_time_ordered(self):
        self.report()
        check = {"check_id": "check-1", "status": "failed",
                 "checked_at": self.now.isoformat(), "error": "Repository check failed"}

        def post(payload, platform="e-learning"):
            return self.http(f"/api/backups/{platform}/checks", "POST", payload,
                             token=self.tokens[platform])[0]

        self.assertEqual(post(check), 200)
        self.assertEqual(post(check), 200)
        self.assertEqual(post({**check, "status": "passed", "error": ""}), 409)
        old = {**check, "check_id": "old-check", "status": "passed", "error": "",
               "checked_at": (self.now - timedelta(days=1)).isoformat()}
        self.assertEqual(post(old), 200)
        platform = self.overview()["platforms"][0]
        self.assertEqual(platform["status"], "successful")
        self.assertEqual(platform["integrity_history"][0]["status"], "failed")
        self.assertEqual(len(platform["integrity_history"]), 2)
        self.assertEqual(self.db.scalar(select(func.count(BackupCheck.id))), 2)
        self.assertEqual(self.http("/api/backups/e-learning/checks", "POST", check,
                                   token=self.tokens["moodle"])[0], 401)
        with self.assertRaises(ValidationError):
            IntegrityReport(**{**check, "checked_at": "2026-01-01T00:00:00"})
        with self.assertRaises(ValidationError):
            IntegrityReport(**{**check, "error": ""})

    def test_platforms_have_independent_run_ids_and_integrity_does_not_invent_a_backup(self):
        self.assertEqual(self.report()[0], 200)
        self.assertEqual(self.report(platform="insight")[0], 200)
        check = {"check_id": "moodle-check", "status": "passed",
                 "checked_at": self.now.isoformat()}
        self.assertEqual(self.http("/api/backups/moodle/checks", "POST", check,
                                   token=self.tokens["moodle"])[0], 200)
        overview = self.overview()
        self.assertEqual(overview["counts"]["successful"], 2)
        self.assertEqual(overview["platforms"][2]["status"], "unknown")
        self.assertEqual(overview["platforms"][2]["integrity_history"][0]["status"], "passed")
        self.assertEqual(overview["platforms"][3]["history"], [])

    def test_history_is_bounded_without_losing_old_last_success(self):
        self.report()
        for index in range(HISTORY_LIMIT + 2):
            payload = {**self.payload, "run_id": f"failure-{index}", "status": "failed",
                       "started_at": (self.now + timedelta(seconds=index)).isoformat(),
                       "completed_at": (self.now + timedelta(seconds=index)).isoformat(),
                       "error": "Failed"}
            self.assertEqual(self.report(payload)[0], 200)
        platform = self.overview(self.now + timedelta(minutes=1))["platforms"][0]
        self.assertEqual(len(platform["history"]), HISTORY_LIMIT)
        self.assertEqual(platform["history"][0]["run_id"], f"failure-{HISTORY_LIMIT + 1}")
        self.assertEqual(platform["last_successful_at"], self.now.isoformat())
        self.assertEqual(self.db.scalar(select(func.count(BackupRun.id))), HISTORY_LIMIT + 3)

    def test_model_requires_timezone_and_explicit_schedule(self):
        for field in ("next_scheduled_at", "overdue_at"):
            values = dict(self.payload)
            del values[field]
            with self.assertRaises(ValidationError):
                BackupReport(**values)


if __name__ == "__main__":
    unittest.main()
