"""Web app CRUD - Session contract. `findings` count is computed on read
via GSI1, not stored - see cloud-api-design.md §2's "deliberate v0.1
simplifications" for why (backend-integration.md already flagged the
mock data's stored count as wrong).
"""

from __future__ import annotations

from datetime import datetime, timezone

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from victor_cloud_api.db import account_pk, get_table, gsi1_session_pk, session_sk
from victor_cloud_api.deps import get_current_account_id
from victor_cloud_api.dynamo_utils import build_update
from victor_cloud_api.models import AuthorizationRecord, Session, SessionStatus

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=2048)
    scope: str = Field(min_length=1, max_length=2000)
    authorization: AuthorizationRecord | None = None

    @model_validator(mode="after")
    def bind_authorization(self):
        if self.authorization:
            if self.authorization.target != self.target or self.authorization.reference != self.scope:
                raise ValueError("Authorization target/reference must match the session")
            if self.authorization.expires_at <= datetime.now(timezone.utc):
                raise ValueError("Authorization must expire in the future")
        return self

    @field_validator("name", "target", "scope")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be blank")
        return value


class PatchSessionRequest(BaseModel):
    status: SessionStatus


class SessionOut(Session):
    findings: int = 0


def _findings_count(table, session_id: str) -> int:
    resp = table.query(
        IndexName="GSI1",
        KeyConditionExpression="gsi1pk = :pk",
        ExpressionAttributeValues={":pk": gsi1_session_pk(session_id)},
        Select="COUNT",
    )
    return resp["Count"]


@router.get("", response_model=list[SessionOut])
def list_sessions(account_id: str = Depends(get_current_account_id)) -> list[SessionOut]:
    table = get_table()
    resp = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": "SESSION#"},
    )
    return [
        SessionOut(**item, findings=_findings_count(table, item["id"]))
        for item in resp.get("Items", [])
    ]


@router.post("", response_model=SessionOut)
def create_session(
    body: CreateSessionRequest, account_id: str = Depends(get_current_account_id)
) -> SessionOut:
    session = Session(account_id=account_id, **body.model_dump())
    table = get_table()
    table.put_item(
        Item={"PK": account_pk(account_id), "SK": session_sk(session.id), **session.model_dump(mode="json")}
    )
    return SessionOut(**session.model_dump(), findings=0)


@router.patch("/{session_id}", response_model=SessionOut)
def patch_session(
    session_id: str, body: PatchSessionRequest, account_id: str = Depends(get_current_account_id)
) -> SessionOut:
    table = get_table()
    key = {"PK": account_pk(account_id), "SK": session_sk(session_id)}
    expr, names, values = build_update({"status": body.status, "updated": _now()})

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
            raise HTTPException(status_code=404, detail="Session not found") from None
        raise

    item = table.get_item(Key=key)["Item"]
    return SessionOut(**item, findings=_findings_count(table, session_id))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
