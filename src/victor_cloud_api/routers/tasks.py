"""POST /v1/sessions/{session_id}/tasks - the web app's task-creation
entry point (BACKLOG.md M0.5 in victor-recon-console). Queues a task
for a collector to pick up on its next GET /v1/collectors/tasks poll.
Cognito-authenticated (the web app, not a collector) - the counterpart
to collectors.py's collector-authenticated routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from victor_cloud_api.db import account_pk, collector_pk, get_table, gsi3_task_sk, query_all, session_sk, task_sk
from victor_cloud_api.deps import get_current_account_id
from victor_cloud_api.models import Task
from victor_cloud_api.scope import validate_authorization
from victor_cloud_api.target_validation import validate_target

router = APIRouter(prefix="/v1/sessions", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    plugin: str
    target: str = Field(min_length=1, max_length=2048)
    safe_mode: bool = True

    @field_validator("plugin")
    @classmethod
    def supported_plugin(cls, value: str) -> str:
        if value not in {"service-inventory", "tls-review", "http-header-audit"}:
            raise ValueError("Unsupported cloud plugin; credentialed plugins are not enabled yet")
        return value

    @field_validator("target")
    @classmethod
    def safe_target(cls, value: str) -> str:
        value = value.strip()
        return validate_target(value)


class TaskOut(Task):
    pass


def _assign_collector(table, account_id: str) -> str:
    """v0.1 assignment: auto-assign when exactly one collector exists -
    true for every Community-tier account, since that tier is capped at
    one (cloud-api-design.md §8's open question on multi-collector
    assignment stays open; refuse rather than silently guessing when
    there's more than one candidate).
    """
    collectors = query_all(table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": "COLLECTOR#"},
    )
    if not collectors:
        raise HTTPException(status_code=409, detail="No collector enrolled for this account yet.")
    if len(collectors) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple collectors enrolled - task assignment isn't built yet.",
        )
    return collectors[0]["collector_id"]


@router.post("/{session_id}/tasks", response_model=TaskOut)
def create_task(
    session_id: str, body: CreateTaskRequest, account_id: str = Depends(get_current_account_id)
) -> TaskOut:
    table = get_table()

    session_item = table.get_item(
        Key={"PK": account_pk(account_id), "SK": session_sk(session_id)}
    ).get("Item")
    if session_item is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session_item.get("status") != "Active":
        raise HTTPException(status_code=409, detail="Only active sessions can run recon")
    if body.target != session_item["target"]:
        raise HTTPException(status_code=422, detail="Task target must match the session target")
    if not session_item.get("scope", "").strip():
        raise HTTPException(status_code=422, detail="Record an authorization scope first")
    if not body.safe_mode:
        raise HTTPException(status_code=422, detail="Cloud tasks currently require safe mode")
    authorization = validate_authorization(session_item, body.plugin, body.target)
    if body.plugin != "http-header-audit" and "://" in body.target:
        raise HTTPException(status_code=422, detail="TLS and service checks require a hostname or IP, not a URL")

    collector_id = _assign_collector(table, account_id)

    task = Task(
        account_id=account_id,
        collector_id=collector_id,
        session_id=session_id,
        plugin=body.plugin,
        target=body.target,
        safe_mode=body.safe_mode,
        requires_claim=True,
        authorization_expires_at=authorization.expires_at.isoformat(),
    )
    table.put_item(
        Item={
            "PK": collector_pk(collector_id),
            "SK": task_sk(task.task_id),
            "gsi3pk": account_pk(account_id),
            "gsi3sk": gsi3_task_sk(task.task_id),
            **task.model_dump(),
        }
    )

    return TaskOut(**task.model_dump())


@router.get("/{session_id}/tasks", response_model=list[TaskOut])
def list_session_tasks(session_id: str, account_id: str = Depends(get_current_account_id)):
    table = get_table()
    if not table.get_item(Key={"PK": account_pk(account_id), "SK": session_sk(session_id)}).get("Item"):
        raise HTTPException(status_code=404, detail="Session not found")
    items = query_all(table, IndexName="GSI3", KeyConditionExpression="gsi3pk = :pk",
                      ExpressionAttributeValues={":pk": account_pk(account_id)})
    return sorted([TaskOut(**item) for item in items if item.get("session_id") == session_id],
                  key=lambda task: task.created_at, reverse=True)
