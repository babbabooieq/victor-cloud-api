"""Collector-facing wire protocol - docs/collector-design.md §7 in
victor-recon-console. enroll, heartbeat, and tasks are real; observations
(deep AI triage's entry point) is the one piece still to add.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from victor_cloud_api.auth import (
    ENROLLMENT_TOKEN_TTL_SECONDS,
    consume_enrollment_token,
    generate_collector_credential,
    has_capacity,
    hash_secret,
    issue_enrollment_token,
    resolve_collector_by_credential,
)
from victor_cloud_api.db import (
    account_pk,
    collector_account_sk,
    collector_pk,
    get_table,
    gsi2_cred_hash_pk,
    query_all,
)
from victor_cloud_api import execution
from victor_cloud_api.deps import get_current_account_id
from victor_cloud_api.models import Collector

router = APIRouter(prefix="/v1/collectors", tags=["collectors"])

MIN_SUPPORTED_VERSION = "0.2.0"


@router.get("", response_model=list[Collector])
def list_collectors(account_id: str = Depends(get_current_account_id)):
    items = query_all(get_table(), KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                      ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": "COLLECTOR#"})
    return [Collector(**item) for item in items]


def _bearer_token(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Expected 'Authorization: Bearer <token>'")
    return authorization.removeprefix("Bearer ").strip()


def get_authenticated_collector(authorization: str = Header(...)) -> dict:
    """Shared auth dependency for every collector-facing route after
    enroll - one GSI2 lookup, no session state."""
    token = _bearer_token(authorization)
    collector = resolve_collector_by_credential(get_table(), token)
    if collector is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked collector credential")
    return collector


class EnrollmentTokenResponse(BaseModel):
    token: str
    expires_in_seconds: int


@router.post("/enrollment-tokens", response_model=EnrollmentTokenResponse)
def create_enrollment_token(
    account_id: str = Depends(get_current_account_id),
) -> EnrollmentTokenResponse:
    """Cognito-authenticated (a human, not a collector) - the "Add a
    Collector" button's backing endpoint. Generates the one-time token a
    collector then exchanges via POST /enroll. Was previously only an
    internal function (issue_enrollment_token); this is the real HTTP
    entry point the web app needs to call it, added once the frontend
    integration actually needed it rather than speculatively.
    """
    token = issue_enrollment_token(get_table(), account_id)
    return EnrollmentTokenResponse(token=token, expires_in_seconds=ENROLLMENT_TOKEN_TTL_SECONDS)


class EnrollRequest(BaseModel):
    hostname: str
    os: str
    arch: str
    version: str


class EnrollResponse(BaseModel):
    collector_id: str
    credential: str
    control_plane_id: str


@router.post("/enroll", response_model=EnrollResponse)
def enroll(body: EnrollRequest, authorization: str = Header(...)) -> EnrollResponse:
    token = _bearer_token(authorization)
    table = get_table()

    account_id = consume_enrollment_token(table, token)
    if account_id is None:
        raise HTTPException(
            status_code=401, detail="Enrollment token is invalid, expired, or already used"
        )

    account = table.get_item(Key={"PK": account_pk(account_id), "SK": "META"}).get("Item")
    if account is None:
        # The token was valid but its account is gone - a data integrity
        # problem, not a client error.
        raise HTTPException(status_code=500, detail="Enrollment token referenced a missing account")

    tier = account.get("tier", "community")
    if not has_capacity(table, account_id, tier):
        raise HTTPException(
            status_code=403,
            detail=f"Collector limit reached for the '{tier}' tier. Upgrade or remove a collector.",
        )

    collector = Collector(
        account_id=account_id,
        hostname=body.hostname,
        os=body.os,
        arch=body.arch,
        version=body.version,
    )
    credential = generate_collector_credential()

    table.put_item(
        Item={
            "PK": account_pk(account_id),
            "SK": collector_account_sk(collector.collector_id),
            "gsi2pk": gsi2_cred_hash_pk(hash_secret(credential)),
            **collector.model_dump(),
        }
    )

    return EnrollResponse(
        collector_id=collector.collector_id,
        credential=credential,
        control_plane_id=account_id,
    )


class HeartbeatRequest(BaseModel):
    version: str
    status: Literal["idle", "running"]


class HeartbeatResponse(BaseModel):
    min_supported_version: str


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    body: HeartbeatRequest, collector: dict = Depends(get_authenticated_collector)
) -> HeartbeatResponse:
    table = get_table()
    table.update_item(
        Key={"PK": collector["PK"], "SK": collector["SK"]},
        UpdateExpression="SET #status = :status, version = :version, last_heartbeat = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": body.status,
            ":version": body.version,
            ":now": datetime.now(timezone.utc).isoformat(),
        },
    )
    # Never force-updates the collector - it's the operator's call. See
    # collector-design.md §7.2.
    return HeartbeatResponse(min_supported_version=MIN_SUPPORTED_VERSION)


class TaskOut(BaseModel):
    task_id: str
    session_id: str
    plugin: str
    target: str
    safe_mode: bool
    claim_required: bool = True


class TasksResponse(BaseModel):
    tasks: list[TaskOut]


@router.get("/tasks", response_model=TasksResponse)
def get_tasks(collector: dict = Depends(get_authenticated_collector)) -> TasksResponse:
    table = get_table()
    items = execution.available_tasks(table, collector)
    return TasksResponse(
        tasks=[
            TaskOut(
                task_id=item["task_id"],
                session_id=item["session_id"],
                plugin=item["plugin"],
                target=item["target"],
                safe_mode=item["safe_mode"],
            )
            for item in items
        ]
    )


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: str, collector: dict = Depends(get_authenticated_collector)):
    return execution.claim(get_table(), collector, task_id)


class TaskResultRequest(BaseModel):
    status: Literal["empty", "failed"]
    lease_token: str | None = None
    error: str | None = Field(default=None, max_length=500)


@router.post("/tasks/{task_id}/result")
def task_result(task_id: str, body: TaskResultRequest, collector: dict = Depends(get_authenticated_collector)):
    execution.finish(get_table(), collector, task_id, status=body.status,
                     token=body.lease_token, error=body.error)
    return {"status": body.status}
