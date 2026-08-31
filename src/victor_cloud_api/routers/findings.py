"""Web app CRUD - docs/backend-integration.md's Finding contract, mapped
to routes per docs/cloud-api-design.md §4.
"""

from __future__ import annotations

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from victor_cloud_api.db import account_pk, finding_sk, get_table, gsi1_finding_sk, gsi1_session_pk
from victor_cloud_api.deps import get_current_account_id
from victor_cloud_api.dynamo_utils import build_update
from victor_cloud_api.models import Finding, Severity

router = APIRouter(prefix="/v1/findings", tags=["findings"])


class CreateFindingRequest(BaseModel):
    session_id: str
    title: str
    severity: Severity
    confidence: int
    asset: str
    evidence: str
    remediation: str
    tags: list[str] = []


class PatchFindingRequest(BaseModel):
    severity: Severity | None = None
    owner: str | None = None
    tags: list[str] | None = None
    reviewed: bool | None = None


@router.get("", response_model=list[Finding])
def list_findings(account_id: str = Depends(get_current_account_id)) -> list[Finding]:
    table = get_table()
    resp = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": "FINDING#"},
    )
    return [Finding(**item) for item in resp.get("Items", [])]


@router.post("", response_model=Finding)
def create_finding(
    body: CreateFindingRequest, account_id: str = Depends(get_current_account_id)
) -> Finding:
    finding = Finding(account_id=account_id, **body.model_dump())
    table = get_table()
    table.put_item(
        Item={
            "PK": account_pk(account_id),
            "SK": finding_sk(finding.id),
            "gsi1pk": gsi1_session_pk(finding.session_id),
            "gsi1sk": gsi1_finding_sk(finding.id),
            **finding.model_dump(),
        }
    )
    return finding


@router.patch("/{finding_id}", response_model=Finding)
def patch_finding(
    finding_id: str, body: PatchFindingRequest, account_id: str = Depends(get_current_account_id)
) -> Finding:
    table = get_table()
    key = {"PK": account_pk(account_id), "SK": finding_sk(finding_id)}
    updates = body.model_dump(exclude_unset=True)

    if updates:
        expr, names, values = build_update(updates)
        try:
            table.update_item(
                Key=key,
                UpdateExpression=expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise HTTPException(status_code=404, detail="Finding not found") from None
            raise

    item = table.get_item(Key=key).get("Item")
    if item is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return Finding(**item)
