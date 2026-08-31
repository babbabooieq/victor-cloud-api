"""POST /v1/sessions/{session_id}/tasks - the web app's task-creation
entry point (BACKLOG.md M0.5 in victor-recon-console). Queues a task
for a collector to pick up on its next GET /v1/collectors/tasks poll.
Cognito-authenticated (the web app, not a collector) - the counterpart
to collectors.py's collector-authenticated routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from victor_cloud_api.db import account_pk, collector_pk, get_table, gsi3_task_sk, session_sk, task_sk
from victor_cloud_api.deps import get_current_account_id
from victor_cloud_api.models import Task

router = APIRouter(prefix="/v1/sessions", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    plugin: str
    target: str
    safe_mode: bool = True


class TaskOut(BaseModel):
    task_id: str
    session_id: str
    collector_id: str
    plugin: str
    target: str
    safe_mode: bool
    status: str


def _assign_collector(table, account_id: str) -> str:
    """v0.1 assignment: auto-assign when exactly one collector exists -
    true for every Community-tier account, since that tier is capped at
    one (cloud-api-design.md §8's open question on multi-collector
    assignment stays open; refuse rather than silently guessing when
    there's more than one candidate).
    """
    resp = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": "COLLECTOR#"},
    )
    collectors = resp.get("Items", [])
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

    collector_id = _assign_collector(table, account_id)

    task = Task(
        account_id=account_id,
        collector_id=collector_id,
        session_id=session_id,
        plugin=body.plugin,
        target=body.target,
        safe_mode=body.safe_mode,
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

    return TaskOut(
        task_id=task.task_id,
        session_id=task.session_id,
        collector_id=collector_id,
        plugin=task.plugin,
        target=task.target,
        safe_mode=task.safe_mode,
        status=task.status,
    )
