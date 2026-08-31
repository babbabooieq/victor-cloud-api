"""Web app CRUD - Target contract. No PATCH: backend-integration.md's
mock UI only ever adds targets, never edits one in place.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from victor_cloud_api.db import account_pk, get_table, target_sk
from victor_cloud_api.deps import get_current_account_id
from victor_cloud_api.models import TargetRow

router = APIRouter(prefix="/v1/targets", tags=["targets"])


class CreateTargetRequest(BaseModel):
    host: str
    ip: str
    scope: str
    auth: str
    services: int = 0
    os: str = "Unknown"


@router.get("", response_model=list[TargetRow])
def list_targets(account_id: str = Depends(get_current_account_id)) -> list[TargetRow]:
    table = get_table()
    resp = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": "TARGET#"},
    )
    return [TargetRow(**item) for item in resp.get("Items", [])]


@router.post("", response_model=TargetRow)
def create_target(
    body: CreateTargetRequest, account_id: str = Depends(get_current_account_id)
) -> TargetRow:
    target = TargetRow(account_id=account_id, **body.model_dump())
    table = get_table()
    table.put_item(
        Item={"PK": account_pk(account_id), "SK": target_sk(target.id), **target.model_dump()}
    )
    return target
