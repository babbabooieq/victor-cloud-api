"""DynamoDB single-table access - schema per docs/cloud-api-design.md §2
in victor-recon-console. Key-builders live here so every router constructs
keys the same way; callers use boto3's Table resource directly for
get/put/query rather than a bespoke ORM layer on top of it.
"""

from __future__ import annotations

import os

import boto3

TABLE_NAME = os.environ.get("VICTOR_TABLE_NAME", "victor-recon-api")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_dynamodb = None


def get_table():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _dynamodb.Table(TABLE_NAME)


# --- Key builders --------------------------------------------------------
# Primary table: PK/SK. GSI1: gsi1pk/gsi1sk. GSI2: gsi2pk/gsi2sk. GSI3: gsi3pk/gsi3sk.


def account_pk(account_id: str) -> str:
    return f"ACCOUNT#{account_id}"


def account_meta_sk() -> str:
    return "META"


def collector_account_sk(collector_id: str) -> str:
    return f"COLLECTOR#{collector_id}"


def collector_pk(collector_id: str) -> str:
    return f"COLLECTOR#{collector_id}"


def session_sk(session_id: str) -> str:
    return f"SESSION#{session_id}"


def target_sk(target_id: str) -> str:
    return f"TARGET#{target_id}"


def finding_sk(finding_id: str) -> str:
    return f"FINDING#{finding_id}"


def message_sk(session_id: str, message_id: str) -> str:
    return f"MSG#{session_id}#{message_id}"


def task_sk(task_id: str) -> str:
    return f"TASK#{task_id}"


def observation_sk(session_id: str, observation_id: str) -> str:
    # Mirrors the MSG#<session>#<msg> pattern - session-scoped, chronological
    # by insertion. Not in cloud-api-design.md §2's original table (a real
    # gap - RawObservation was specified in §5 but never given a key shape);
    # fixed there alongside this.
    return f"OBS#{session_id}#{observation_id}"


def gsi1_session_pk(session_id: str) -> str:
    return f"SESSION#{session_id}"


def gsi1_finding_sk(finding_id: str) -> str:
    return f"FINDING#{finding_id}"


def gsi2_cred_hash_pk(cred_hash: str) -> str:
    return f"CRED_HASH#{cred_hash}"


def gsi3_task_sk(task_id: str) -> str:
    return f"TASK#{task_id}"


# --- Table definition (used by both the provisioning script and tests) ---


def table_schema() -> dict:
    """The full create_table() kwargs - one definition, so the real table
    and moto's in-memory table in tests can never drift apart."""
    return {
        "TableName": TABLE_NAME,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "gsi1pk", "AttributeType": "S"},
            {"AttributeName": "gsi1sk", "AttributeType": "S"},
            {"AttributeName": "gsi2pk", "AttributeType": "S"},
            {"AttributeName": "gsi3pk", "AttributeType": "S"},
            {"AttributeName": "gsi3sk", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI2",
                "KeySchema": [{"AttributeName": "gsi2pk", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI3",
                "KeySchema": [
                    {"AttributeName": "gsi3pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi3sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    }


def create_table_if_missing() -> None:
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    client = _dynamodb.meta.client
    existing = client.list_tables()["TableNames"]
    if TABLE_NAME not in existing:
        client.create_table(**table_schema())
        client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
