"""Attributed, immutable evidence ingestion and account-authenticated reads."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from decimal import Decimal

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from victor_cloud_api import execution
from victor_cloud_api.db import account_pk, get_table, observation_sk, query_all, session_sk
from victor_cloud_api.deps import get_current_account_id
from victor_cloud_api.models import RawObservation
from victor_cloud_api.routers.collectors import get_authenticated_collector

router = APIRouter(prefix="/v1/sessions", tags=["observations"])


class ObservationIn(BaseModel):
    id: str | None = Field(default=None, max_length=128)
    collected_at: datetime | None = None
    plugin: str
    target: str
    raw: dict
    redacted: bool = False


class UploadObservationsRequest(BaseModel):
    task_id: str
    lease_token: str | None = None
    collector_version: str | None = Field(default=None, max_length=40)
    observations: list[ObservationIn] = Field(max_length=1000)


class UploadObservationsResponse(BaseModel):
    accepted: int


class ObservationOut(RawObservation):
    session_id: str
    task_id: str
    collector_id: str
    finalized: bool = False


def _session(table, account_id: str, session_id: str) -> dict:
    item = table.get_item(Key={"PK": account_pk(account_id), "SK": session_sk(session_id)},
                          ConsistentRead=True).get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    return item


@router.get("/{session_id}/observations", response_model=list[ObservationOut])
def list_observations(session_id: str, account_id: str = Depends(get_current_account_id)):
    table = get_table()
    _session(table, account_id, session_id)
    items = query_all(table, ConsistentRead=True,
                      KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                      ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": observation_sk(session_id, "")})
    tasks = {}
    result = []
    for item in items:
        key = (item["collector_id"], item["task_id"])
        if key not in tasks:
            tasks[key] = table.get_item(Key=execution.task_key(*key), ConsistentRead=True).get("Item", {})
        task = tasks[key]
        finalized = task.get("status") == "completed" and bool(item.get("batch_fingerprint")) and item.get("batch_fingerprint") == task.get("upload_fingerprint")
        result.append(ObservationOut(**item, finalized=finalized))
    return sorted(result, key=lambda obs: obs.collected_at)


@router.post("/{session_id}/observations", response_model=UploadObservationsResponse, status_code=202)
def upload_observations(session_id: str, body: UploadObservationsRequest,
                        collector: dict = Depends(get_authenticated_collector)):
    table = get_table()
    _session(table, collector["account_id"], session_id)
    task = execution.assigned_task(table, collector, body.task_id)
    if task["session_id"] != session_id:
        raise HTTPException(status_code=422, detail="Task does not belong to this session")
    if any(obs.plugin != task["plugin"] for obs in body.observations):
        raise HTTPException(status_code=422, detail="Observation plugin does not match assigned task")
    source_ids = [obs.id for obs in body.observations if obs.id is not None]
    if len(source_ids) != len(set(source_ids)):
        raise HTTPException(status_code=422, detail="Observation IDs must be unique within a batch")
    payloads = [obs.model_dump(mode="json", exclude_none=True) for obs in body.observations]
    serialized = json.dumps({"version": body.collector_version, "observations": payloads}, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode()) > 250_000:
        raise HTTPException(status_code=413, detail="Observation batch is too large")
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    if task["status"] == "completed":
        if task.get("upload_fingerprint") != fingerprint:
            raise HTTPException(status_code=409, detail="Completed task evidence is immutable")
        return UploadObservationsResponse(accepted=len(body.observations))
    execution.validate_lease(task, body.lease_token)
    for index, payload in enumerate(payloads):
        source_id = payload.pop("id", None) or f"{fingerprint}:{index}"
        obs_id = "obs_" + hashlib.sha256(f"{body.task_id}:{source_id}".encode()).hexdigest()
        obs = RawObservation(id=obs_id, **payload)
        item = {"PK": account_pk(collector["account_id"]), "SK": observation_sk(session_id, obs_id),
                "session_id": session_id, "task_id": body.task_id,
                "collector_id": collector["collector_id"], "batch_fingerprint": fingerprint,
                "payload_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                **obs.model_dump()}
        try:
            table.put_item(Item=json.loads(json.dumps(item), parse_float=Decimal), ConditionExpression="attribute_not_exists(PK)")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            old = table.get_item(Key={"PK": item["PK"], "SK": item["SK"]}, ConsistentRead=True)["Item"]
            if old.get("payload_hash") != item["payload_hash"] or old.get("batch_fingerprint") != fingerprint:
                raise HTTPException(status_code=409, detail="Observation ID cannot be overwritten") from None
    values = {":previous": task["status"], ":completed": "completed", ":finished": execution.now_iso(),
              ":count": len(body.observations), ":fingerprint": fingerprint, ":version": body.collector_version}
    condition = "#status = :previous"
    if task["status"] == "running":
        condition += " AND lease_token = :token AND lease_until > :now"
        values.update({":token": body.lease_token, ":now": int(time.time())})
    try:
        table.update_item(Key=execution.task_key(collector["collector_id"], body.task_id),
                          UpdateExpression="SET #status = :completed, finished_at = :finished, observation_count = :count, upload_fingerprint = :fingerprint, executed_version = :version REMOVE lease_token, lease_until",
                          ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues=values,
                          ConditionExpression=condition)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            latest = execution.assigned_task(table, collector, body.task_id)
            if latest.get("status") != "completed" or latest.get("upload_fingerprint") != fingerprint:
                raise HTTPException(status_code=409, detail="Task changed during upload; evidence not finalized") from None
        else:
            raise
    return UploadObservationsResponse(accepted=len(body.observations))
