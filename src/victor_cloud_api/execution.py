"""Conditional task leases. No new table/index or third auth mechanism."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from fastapi import HTTPException

from victor_cloud_api.db import collector_pk, query_all, task_sk

MAX_ATTEMPTS = 3
LEASE_SECONDS = 600  # Current plugins have bounded execution well below this.


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_key(collector_id: str, task_id: str) -> dict:
    return {"PK": collector_pk(collector_id), "SK": task_sk(task_id)}


def assigned_task(table, collector: dict, task_id: str) -> dict:
    item = table.get_item(Key=task_key(collector["collector_id"], task_id), ConsistentRead=True).get("Item")
    if not item or item.get("account_id") != collector["account_id"]:
        raise HTTPException(status_code=404, detail="Assigned task not found")
    return item


def available_tasks(table, collector: dict) -> list[dict]:
    items = query_all(table, ConsistentRead=True,
                      KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                      ExpressionAttributeValues={":pk": collector_pk(collector["collector_id"]), ":prefix": "TASK#"})
    available = []
    now = int(time.time())
    for item in items:
        expires = item.get("authorization_expires_at")
        if item.get("status") == "pending" and expires and datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
            try:
                table.update_item(Key=task_key(collector["collector_id"], item["task_id"]),
                                  UpdateExpression="SET #status = :failed, finished_at = :finished, #error = :error",
                                  ExpressionAttributeNames={"#status": "status", "#error": "error"},
                                  ExpressionAttributeValues={":pending": "pending", ":failed": "failed", ":finished": now_iso(), ":error": "Authorization expired before execution"},
                                  ConditionExpression="#status = :pending")
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
            continue
        if item.get("status") == "running" and int(item.get("lease_until", 0)) <= now:
            exhausted = int(item.get("attempts", 0)) >= MAX_ATTEMPTS
            values = {":now": now, ":running": "running", ":status": "failed" if exhausted else "pending"}
            update = "SET #status = :status REMOVE lease_token, lease_until"
            if exhausted:
                update = "SET #status = :status, finished_at = :finished, #error = :error REMOVE lease_token, lease_until"
                values.update({":finished": now_iso(), ":error": "Collector lease expired after maximum attempts"})
            names = {"#status": "status"}
            if exhausted:
                names["#error"] = "error"
            try:
                table.update_item(Key=task_key(collector["collector_id"], item["task_id"]),
                                  UpdateExpression=update, ExpressionAttributeNames=names,
                                  ExpressionAttributeValues=values,
                                  ConditionExpression="#status = :running AND lease_until <= :now")
                item["status"] = values[":status"]
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                continue
        if item.get("status") == "pending":
            available.append(item)
    return sorted(available, key=lambda item: (item.get("created_at", ""), item["task_id"]))


def claim(table, collector: dict, task_id: str) -> dict:
    item = assigned_task(table, collector, task_id)
    expires = item.get("authorization_expires_at")
    if expires and datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="Authorization expired")
    token = uuid.uuid4().hex
    try:
        result = table.update_item(
            Key=task_key(collector["collector_id"], task_id),
            UpdateExpression="SET #status = :running, lease_token = :token, lease_until = :until, attempts = if_not_exists(attempts, :zero) + :one",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":pending": "pending", ":running": "running", ":token": token,
                                       ":until": int(time.time()) + LEASE_SECONDS, ":zero": 0, ":one": 1, ":max": MAX_ATTEMPTS},
            ConditionExpression="#status = :pending AND (attribute_not_exists(attempts) OR attempts < :max)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409, detail="Task is no longer available") from None
        raise
    item = result["Attributes"]
    return {"lease_token": token, "lease_until": int(item["lease_until"])}


def validate_lease(item: dict, token: str | None) -> None:
    if item.get("status") == "running":
        if not token or item.get("lease_token") != token or int(item.get("lease_until", 0)) <= int(time.time()):
            raise HTTPException(status_code=409, detail="Task lease expired or does not match")
    elif item.get("status") == "pending" and item.get("requires_claim"):
        raise HTTPException(status_code=409, detail="Claim the task before submitting a result")
    elif item.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Task is already terminal")


def finish(table, collector: dict, task_id: str, *, status: str, token: str | None, error: str | None = None) -> None:
    item = assigned_task(table, collector, task_id)
    validate_lease(item, token)
    condition = "#status = :previous"
    values = {":previous": item["status"], ":status": status, ":finished": now_iso(), ":error": error}
    if item["status"] == "running":
        condition += " AND lease_token = :token AND lease_until > :now"
        values.update({":token": token, ":now": int(time.time())})
    try:
        table.update_item(Key=task_key(collector["collector_id"], task_id),
                          UpdateExpression="SET #status = :status, finished_at = :finished, #error = :error REMOVE lease_until, lease_token",
                          ExpressionAttributeNames={"#status": "status", "#error": "error"},
                          ExpressionAttributeValues=values, ConditionExpression=condition)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409, detail="Task changed while finishing") from None
        raise
