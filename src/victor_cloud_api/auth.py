"""Collector credential + enrollment token handling.

Two different secrets, two different lifetimes:
- Enrollment token: single-use, short-lived (15 min), created when someone
  clicks "Add a Collector" in the dashboard (not built yet - see
  issue_enrollment_token, called directly for now, not yet its own route).
- Collector credential: long-lived, per-collector, individually revocable
  (docs/cloud-api-design.md §3 / collector-design.md §7.1).

Neither is stored in plaintext - only a SHA-256 hash, so a DynamoDB read
alone can't leak a usable secret.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from botocore.exceptions import ClientError

from victor_cloud_api.db import account_pk, gsi2_cred_hash_pk

ENROLLMENT_TOKEN_TTL_SECONDS = 15 * 60

# None = unlimited. Matches docs/cloud-api-design.md §4's enrollment-time
# tier enforcement note.
TIER_COLLECTOR_LIMITS: dict[str, int | None] = {
    "community": 1,
    "pro": 3,
    "team": None,
    "msp": None,
}


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def enroll_token_pk(token_hash: str) -> str:
    return f"ENROLL_TOKEN#{token_hash}"


def issue_enrollment_token(table, account_id: str) -> str:
    """Creates a one-time enrollment token for an account. Plaintext is
    returned once and never stored - only its hash persists."""
    token = f"vre_enroll_{secrets.token_hex(16)}"
    table.put_item(
        Item={
            "PK": enroll_token_pk(hash_secret(token)),
            "SK": "META",
            "account_id": account_id,
            "used": False,
            "expires_at": int(time.time()) + ENROLLMENT_TOKEN_TTL_SECONDS,
        }
    )
    return token


def consume_enrollment_token(table, token: str) -> str | None:
    """Atomically marks a token used and returns its account_id, or None
    if it's missing, already used, or expired. The conditional update
    prevents two concurrent enroll requests from both succeeding on the
    same token (replay protection)."""
    token_hash = hash_secret(token)
    try:
        resp = table.update_item(
            Key={"PK": enroll_token_pk(token_hash), "SK": "META"},
            UpdateExpression="SET used = :true",
            ConditionExpression=(
                "attribute_exists(PK) AND used = :false AND expires_at > :now"
            ),
            ExpressionAttributeValues={
                ":true": True,
                ":false": False,
                ":now": int(time.time()),
            },
            ReturnValues="ALL_NEW",
        )
        return resp["Attributes"]["account_id"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


def generate_collector_credential() -> str:
    return f"vre_col_{secrets.token_hex(24)}"


def resolve_collector_by_credential(table, credential: str) -> dict | None:
    """Bearer-token auth for every collector-facing route after enroll:
    one GSI2 query, no scan, resolves straight to the full Collector item
    (account_id included) - see cloud-api-design.md §3."""
    resp = table.query(
        IndexName="GSI2",
        KeyConditionExpression="gsi2pk = :pk",
        ExpressionAttributeValues={":pk": gsi2_cred_hash_pk(hash_secret(credential))},
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def count_active_collectors(table, account_id: str) -> int:
    resp = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": account_pk(account_id), ":prefix": "COLLECTOR#"},
        Select="COUNT",
    )
    return resp["Count"]


def has_capacity(table, account_id: str, tier: str) -> bool:
    limit = TIER_COLLECTOR_LIMITS.get(tier)
    if limit is None:
        return True
    return count_active_collectors(table, account_id) < limit
