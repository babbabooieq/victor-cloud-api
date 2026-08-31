"""POST /v1/sessions/{id}/observations - docs/collector-design.md §7.4.

Collector-authenticated (reuses collectors.py's dependency, not a new
auth scheme). Stores observations as-is; classifying them into real
Findings is deep AI triage (BACKLOG.md M2 in victor-recon-console) -
deliberately out of scope here, per cloud-api-design.md §5.
"""

from __future__ import annotations

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from victor_cloud_api.db import account_pk, collector_pk, get_table, observation_sk, task_sk
from victor_cloud_api.models import RawObservation
from victor_cloud_api.routers.collectors import get_authenticated_collector

router = APIRouter(prefix="/v1/sessions", tags=["observations"])


class ObservationIn(BaseModel):
    plugin: str
    target: str
    raw: dict
    redacted: bool = False


class UploadObservationsRequest(BaseModel):
    task_id: str
    observations: list[ObservationIn]


class UploadObservationsResponse(BaseModel):
    accepted: int


@router.post(
    "/{session_id}/observations", response_model=UploadObservationsResponse, status_code=202
)
def upload_observations(
    session_id: str,
    body: UploadObservationsRequest,
    collector: dict = Depends(get_authenticated_collector),
) -> UploadObservationsResponse:
    table = get_table()
    account_id = collector["account_id"]
    collector_id = collector["collector_id"]

    with table.batch_writer() as batch:
        for obs_in in body.observations:
            obs = RawObservation(**obs_in.model_dump())
            batch.put_item(
                Item={
                    "PK": account_pk(account_id),
                    "SK": observation_sk(session_id, obs.id),
                    "session_id": session_id,
                    "task_id": body.task_id,
                    "collector_id": collector_id,
                    **obs.model_dump(),
                }
            )

    # Best-effort: mark the task completed so it stops showing up in polls.
    # Not fatal if the task_id doesn't exist (e.g. manual/ad-hoc testing) -
    # the observations are already safely stored above regardless.
    try:
        table.update_item(
            Key={"PK": collector_pk(collector_id), "SK": task_sk(body.task_id)},
            UpdateExpression="SET #status = :completed",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":completed": "completed"},
            ConditionExpression="attribute_exists(PK)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

    return UploadObservationsResponse(accepted=len(body.observations))
