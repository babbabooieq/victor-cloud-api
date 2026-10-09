"""Data shapes - mirrors docs/cloud-api-design.md §2 and §4 in
victor-recon-console, and docs/backend-integration.md's web app contract.
Keep these three in sync if any changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

Severity = Literal["Info", "Low", "Medium", "High", "Critical"]
SessionStatus = Literal["Active", "Paused", "Archived"]
CollectorStatus = Literal["idle", "running", "offline"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Account(BaseModel):
    account_id: str = Field(default_factory=lambda: new_id("acct"))
    name: str
    tier: Literal["community", "pro", "team", "msp"] = "community"
    created_at: str = Field(default_factory=_now)


class Collector(BaseModel):
    collector_id: str = Field(default_factory=lambda: new_id("col"))
    account_id: str
    hostname: str
    os: str
    arch: str
    version: str
    status: CollectorStatus = "idle"
    enrolled_at: str = Field(default_factory=_now)
    last_heartbeat: str | None = None


class AuthorizationRecord(BaseModel):
    version: Literal[1] = 1
    reference: str = Field(min_length=1, max_length=2000)
    target: str = Field(min_length=1, max_length=2048)
    allowed_plugins: list[Literal["service-inventory", "tls-review", "http-header-audit"]] = Field(min_length=1, max_length=3)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Authorization expiry must include its timezone")
        return value


class Session(BaseModel):
    id: str = Field(default_factory=lambda: new_id("S"))
    account_id: str
    name: str
    target: str
    scope: str
    status: SessionStatus = "Active"
    updated: str = Field(default_factory=_now)
    authorization: AuthorizationRecord | None = None


class TargetRow(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tgt"))
    account_id: str
    host: str
    ip: str
    scope: str
    auth: str
    services: int = 0
    os: str = "Unknown"


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: new_id("VIC"))
    account_id: str
    session_id: str
    title: str
    severity: Severity
    confidence: int = Field(ge=0, le=100)
    asset: str
    evidence: str
    remediation: str
    tags: list[str] = Field(default_factory=list)
    owner: str | None = None
    reviewed: bool = False


class RawObservation(BaseModel):
    """What a collector uploads - see collector-design.md §6. Not a
    Finding; deep triage (M2, not built here) turns these into one."""

    id: str = Field(default_factory=lambda: new_id("obs"))
    plugin: str
    target: str
    collected_at: str = Field(default_factory=_now)
    raw: dict
    redacted: bool = False

    @field_serializer("raw", when_used="json")
    def serialize_raw(self, raw: dict) -> dict:
        def convert(value):
            if isinstance(value, Decimal):
                return int(value) if value == value.to_integral_value() else float(value)
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, list):
                return [convert(item) for item in value]
            return value
        return convert(raw)


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    account_id: str
    collector_id: str
    session_id: str
    plugin: str
    target: str
    safe_mode: bool = True
    status: Literal["pending", "running", "completed", "empty", "failed", "cancelled"] = "pending"
    created_at: str = Field(default_factory=_now)
    attempts: int = 0
    finished_at: str | None = None
    error: str | None = None
    observation_count: int = 0
    requires_claim: bool = False  # Compatibility for already-queued v0.1 tasks.
    executed_version: str | None = None
    authorization_expires_at: str | None = None
