"""Read-only backup monitoring; scheduling remains on the reporting servers."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BackupCheck, BackupRun

PLATFORMS = {
    "e-learning": "E-Learning",
    "insight": "Insight",
    "moodle": "Moodle LMS",
    "asimolog": "AsimoLog",
}
LEARNING_PLATFORMS = ("e-learning", "moodle")
OTHER_PLATFORMS = ("insight", "asimolog")
DESTINATIONS = {"server": "Server backups", "b2": "Backblaze B2"}
SAST = ZoneInfo("Africa/Johannesburg")
HISTORY_LIMIT = 20


def utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; all stored backup timestamps are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def backup_time(value: str | None) -> str:
    if value is None:
        return "Not reported"
    return datetime.fromisoformat(value).astimezone(SAST).strftime("%d %b %Y %H:%M:%S")


def backup_size(value: int | None) -> str:
    if value is None:
        return "Not reported"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    raise AssertionError("Unreachable byte unit")


def _run_data(run: BackupRun) -> dict:
    return {
        "run_id": run.run_id, "status": run.status,
        "started_at": utc(run.started_at).isoformat(),
        "completed_at": utc(run.completed_at).isoformat() if run.completed_at else None,
        "expected_completion_at": utc(run.expected_completion_at).isoformat() if run.expected_completion_at else None,
        "next_scheduled_at": utc(run.next_scheduled_at).isoformat(),
        "overdue_at": utc(run.overdue_at).isoformat(),
        "destination": run.destination, "processed_bytes": run.processed_bytes,
        "destination_kind": run.destination_kind, "contents": run.contents,
        "repository_bytes": run.repository_bytes, "error": run.error,
        "received_at": utc(run.received_at).isoformat(),
    }


def _destination_overview(db: Session, key: str, kind: str, now: datetime) -> dict:
    runs = list(db.scalars(
        select(BackupRun).where(BackupRun.platform == key, BackupRun.destination_kind == kind)
        .order_by(BackupRun.started_at.desc(), BackupRun.id.desc()).limit(HISTORY_LIMIT)
    ))
    latest = runs[0] if runs else None
    last_completed = db.scalar(
        select(BackupRun.completed_at).where(
            BackupRun.platform == key, BackupRun.destination_kind == kind,
            BackupRun.completed_at.is_not(None),
        ).order_by(BackupRun.completed_at.desc()).limit(1)
    )
    last_success = db.scalar(
    select(BackupRun.completed_at).where(
        BackupRun.platform == key,
        BackupRun.destination_kind == kind,
        BackupRun.status.in_((
            "successful",
            "historical_snapshot",
        )),
    ).order_by(BackupRun.completed_at.desc()).limit(1)
)
    checks = list(db.scalars(
        select(BackupCheck).where(BackupCheck.platform == key, BackupCheck.destination_kind == kind)
        .order_by(BackupCheck.checked_at.desc(), BackupCheck.id.desc()).limit(HISTORY_LIMIT)
    ))
    status, reason = "unknown", "No backup reports received."
    if kind == "b2" and not latest and not checks:
        status, reason = "pending", "Backblaze B2 backups are still being configured. No backup reports received."
    if latest:
        status, reason = latest.status, ""
        if status == "running":
            if latest.expected_completion_at and now > utc(latest.expected_completion_at):
                status, reason = "overdue", "Running backup exceeded its reported completion deadline."
        elif status == "successful" and now > utc(latest.overdue_at):
            status, reason = "overdue", "No new run reported by the server's next-backup deadline."
        elif status == "failed":
            reason = "The latest backup run failed."
    return {
        "kind": kind, "name": DESTINATIONS[kind], "status": status, "reason": reason,
        "last_completed_at": utc(last_completed).isoformat() if last_completed else None,
        "last_successful_at": utc(last_success).isoformat() if last_success else None,
        "latest": _run_data(latest) if latest else None,
        "history": [_run_data(run) for run in runs],
        "integrity_history": [{
            "check_id": check.check_id, "status": check.status,
            "destination_kind": check.destination_kind,
            "checked_at": utc(check.checked_at).isoformat(), "error": check.error,
            "received_at": utc(check.received_at).isoformat(),
        } for check in checks],
    }


def backup_overview(
    db: Session, now: datetime | None = None, platform_keys: tuple[str, ...] | None = None,
) -> dict:
    now = utc(now) if now else datetime.now(timezone.utc)
    platforms = []
    counts = dict.fromkeys(("successful", "failed", "running", "overdue", "unknown"), 0)
    destination_counts = {**counts, "pending": 0}
    for key, name in PLATFORMS.items():
        if platform_keys is not None and key not in platform_keys:
            continue
        kinds = ("server", "b2") if key in LEARNING_PLATFORMS else ("server",)
        destinations = [_destination_overview(db, key, kind, now) for kind in kinds]
        server = destinations[0]

        # Map historical backup statuses for dashboard display.
        def display_status(status):
            if status in ("historical_snapshot", "snapshot_available"):
                return "successful"
            return status

        counts[display_status(server["status"])] += 1

        for destination in destinations:
            destination_counts[display_status(destination["status"])] += 1

        platforms.append({
            **{field: value for field, value in server.items()
               if field not in ("kind", "name")},
            "key": key,
            "name": name,
            "destinations": destinations,
        })
    return {
        "platforms": platforms, "counts": counts, "destination_counts": destination_counts,
        "generated_at": now.isoformat(),
    }
