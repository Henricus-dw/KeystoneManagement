"""Platform-scoped push reporting, separate from signed-in monitoring access."""
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.backups import (
    LEARNING_PLATFORMS, OTHER_PLATFORMS, PLATFORMS,
    backup_overview, backup_size, backup_time, utc,
)
from app.db import get_db
from app.access import can_access_page, guest_platforms
from app.deps import require_user
from app.models import BackupCheck, BackupRun, User
from app.templating import templates

router = APIRouter()
bearer = HTTPBearer(auto_error=False)
Identifier = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")]
ByteCount = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
DestinationKind = Literal["server", "b2"]
BackupContent = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def require_reporter(
    platform: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if platform not in PLATFORMS:
        raise HTTPException(404, "Unknown backup platform")
    tokens = {
        key: os.getenv(f"KEYSTONE_BACKUP_TOKEN_{key.upper().replace('-', '_')}", "")
        for key in PLATFORMS
    }
    token = tokens[platform]
    if len(token) < 32 or not token.isascii() or list(tokens.values()).count(token) != 1:
        raise HTTPException(503, "Backup reporting token is not configured uniquely for this platform")
    if not credentials or not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), token.encode("utf-8"),
    ):
        raise HTTPException(401, "Invalid backup reporting token", headers={"WWW-Authenticate": "Bearer"})
    return platform


class BackupReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Identifier
    destination_kind: DestinationKind = "server"
    status: Literal["running", "successful", "failed"]
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    expected_completion_at: AwareDatetime | None = None
    next_scheduled_at: AwareDatetime
    overdue_at: AwareDatetime
    destination: Annotated[str, Field(min_length=1, max_length=200)]
    contents: Annotated[list[BackupContent], Field(max_length=100)] = Field(default_factory=list)
    processed_bytes: ByteCount | None = None
    repository_bytes: ByteCount | None = None
    error: Annotated[str, Field(max_length=4000)] = ""

    @model_validator(mode="after")
    def validate_run(self) -> "BackupReport":
        future_limit = datetime.now(timezone.utc) + timedelta(minutes=5)
        if self.started_at > future_limit:
            raise ValueError("started_at cannot be in the future")
        if not self.destination.strip():
            raise ValueError("destination must name the backup target")
        if self.next_scheduled_at <= self.started_at or self.overdue_at < self.next_scheduled_at:
            raise ValueError("next_scheduled_at must follow started_at; overdue_at cannot precede it")
        if self.status == "running":
            if self.completed_at is not None or self.expected_completion_at is None:
                raise ValueError("running requires expected_completion_at and no completed_at")
            if self.expected_completion_at <= self.started_at:
                raise ValueError("expected_completion_at must follow started_at")
            if self.error:
                raise ValueError("running reports cannot contain a failure error")
        else:
            if self.completed_at is None or self.completed_at < self.started_at:
                raise ValueError("completed runs require completed_at at or after started_at")
            if self.completed_at > future_limit:
                raise ValueError("completed_at cannot be in the future")
            if self.status == "failed" and not self.error.strip():
                raise ValueError("failed reports require a sanitized error message")
            if self.status == "successful" and self.error:
                raise ValueError("successful reports cannot contain a failure error")
        return self


class IntegrityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: Identifier
    destination_kind: DestinationKind = "server"
    status: Literal["passed", "failed"]
    checked_at: AwareDatetime
    error: Annotated[str, Field(max_length=4000)] = ""

    @model_validator(mode="after")
    def validate_check(self) -> "IntegrityReport":
        if self.checked_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("checked_at cannot be in the future")
        if self.status == "failed" and not self.error.strip():
            raise ValueError("failed checks require a sanitized error message")
        if self.status == "passed" and self.error:
            raise ValueError("passed checks cannot contain a failure error")
        return self


def _stored_values(payload: BaseModel) -> dict:
    return {
        key: utc(value).replace(tzinfo=None) if isinstance(value, datetime) else value
        for key, value in payload.model_dump().items()
    }


def _matches(record: BackupRun | BackupCheck, values: dict) -> bool:
    return all(getattr(record, key) == value for key, value in values.items())


def _validate_destination(platform: str, kind: DestinationKind) -> None:
    if kind == "b2" and platform not in LEARNING_PLATFORMS:
        raise HTTPException(422, "Backblaze B2 reporting is supported for E-Learning and Moodle only")


def _update_run(db: Session, run: BackupRun, values: dict) -> None:
    if _matches(run, values):
        return
    if run.started_at != values["started_at"] or run.status != "running":
        raise HTTPException(409, "Run ID already has different start time or a final result")
    # Guard in SQL too: a concurrent final report must not be overwritten.
    updated_id = db.execute(
        update(BackupRun).where(
            BackupRun.id == run.id, BackupRun.status == "running",
        ).values(**values, received_at=datetime.now(timezone.utc).replace(tzinfo=None))
        .returning(BackupRun.id)
    ).scalar_one_or_none()
    if updated_id is None:
        db.refresh(run)
        if not _matches(run, values):
            raise HTTPException(409, "Run was finalized by another report")


@router.post("/api/backups/{platform}/reports")
def report_backup(
    payload: BackupReport, platform: str = Depends(require_reporter),
    db: Session = Depends(get_db),
):
    _validate_destination(platform, payload.destination_kind)
    values = _stored_values(payload)
    run = db.scalar(select(BackupRun).where(
        BackupRun.platform == platform, BackupRun.run_id == payload.run_id,
        BackupRun.destination_kind == payload.destination_kind,
    ))
    if run:
        _update_run(db, run, values)
    else:
        db.add(BackupRun(platform=platform, **values))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Concurrent retries may both have observed an absent run.
        run = db.scalar(select(BackupRun).where(
            BackupRun.platform == platform, BackupRun.run_id == payload.run_id,
            BackupRun.destination_kind == payload.destination_kind,
        ))
        if run is None:
            raise
        _update_run(db, run, values)
        db.commit()
    return {"ok": True, "run_id": payload.run_id}


@router.post("/api/backups/{platform}/checks")
def report_integrity(
    payload: IntegrityReport, platform: str = Depends(require_reporter),
    db: Session = Depends(get_db),
):
    _validate_destination(platform, payload.destination_kind)
    values = _stored_values(payload)
    check = db.scalar(select(BackupCheck).where(
        BackupCheck.platform == platform, BackupCheck.check_id == payload.check_id,
        BackupCheck.destination_kind == payload.destination_kind,
    ))
    if check:
        if not _matches(check, values):
            raise HTTPException(409, "Check ID already has a different result")
    else:
        db.add(BackupCheck(platform=platform, **values))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            check = db.scalar(select(BackupCheck).where(
                BackupCheck.platform == platform, BackupCheck.check_id == payload.check_id,
                BackupCheck.destination_kind == payload.destination_kind,
            ))
            if check is None:
                raise
            if not _matches(check, values):
                raise HTTPException(409, "Check ID already has a different result")
    return {"ok": True, "check_id": payload.check_id}


@router.get("/api/backups")
def backup_statuses(
    response: Response, user: User = Depends(require_user), db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return backup_overview(db, platform_keys=guest_platforms(user))


@router.get("/backups")
def backups_page(user: User = Depends(require_user)):
    target = "/backups/learning" if can_access_page(user, "backups-learning") else "/backups/apps"
    return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/backups/{group}")
def backup_group_page(
    group: Literal["learning", "apps"],
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
):
    learning = group == "learning"
    return templates.TemplateResponse(request, "backups.html", {
        "user": user, "nav": f"backups-{group}", "group": group,
        "page_title": "E-Learning & Moodle Backups" if learning else "Other App Backups",
        **backup_overview(db, platform_keys=LEARNING_PLATFORMS if learning else OTHER_PLATFORMS),
        "backup_time": backup_time, "backup_size": backup_size,
    }, headers={"Cache-Control": "no-store"})
