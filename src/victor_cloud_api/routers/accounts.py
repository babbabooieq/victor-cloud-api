"""Account provisioning - docs/cloud-api-design.md §2's Account entity.

Cognito doesn't know about DynamoDB, and there's no post-confirmation
Lambda trigger wired up to auto-create the Account META record on
sign-up - a real gap found via live testing against the deployed stack,
not something designed away on purpose. This endpoint is the fix:
idempotent, meant to be called by the frontend right after a successful
sign-in (or sign-up confirmation), safe to call every time.
"""

from __future__ import annotations

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from victor_cloud_api.db import account_meta_sk, account_pk, get_table
from victor_cloud_api.deps import get_current_account_id

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


class AccountOut(BaseModel):
    account_id: str
    tier: str


@router.post("/me", response_model=AccountOut)
def ensure_account(account_id: str = Depends(get_current_account_id)) -> AccountOut:
    table = get_table()
    key = {"PK": account_pk(account_id), "SK": account_meta_sk()}
    try:
        table.put_item(
            Item={**key, "account_id": account_id, "tier": "community"},
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Already provisioned - this endpoint is idempotent by design,
        # not an error.

    item = table.get_item(Key=key)["Item"]
    return AccountOut(account_id=item["account_id"], tier=item["tier"])
