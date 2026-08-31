from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from victor_cloud_api import db

# Tests run outside API Gateway, so they use deps.py's explicit local-dev
# opt-in (X-Account-Id header) rather than real Cognito JWT claims. This
# flag would never be set in the real Lambda's environment.
os.environ["VICTOR_ALLOW_INSECURE_LOCAL_AUTH"] = "1"


@pytest.fixture
def dynamodb_table():
    """Fresh, in-memory DynamoDB table per test via moto - never touches
    real AWS. Resets db.py's cached resource so moto's patching (which
    only applies to resources created while the mock is active) actually
    takes effect."""
    with mock_aws():
        db._dynamodb = None
        db.create_table_if_missing()
        yield db.get_table()
        db._dynamodb = None


@pytest.fixture
def client(dynamodb_table):
    from victor_cloud_api.app import app

    return TestClient(app)


def make_account(table, account_id: str = "acct_test", tier: str = "community") -> str:
    table.put_item(
        Item={
            "PK": db.account_pk(account_id),
            "SK": db.account_meta_sk(),
            "account_id": account_id,
            "name": "Test Account",
            "tier": tier,
        }
    )
    return account_id


def make_expired_token(table, account_id: str) -> str:
    from victor_cloud_api.auth import enroll_token_pk, hash_secret

    token = "vre_enroll_expiredtoken"
    table.put_item(
        Item={
            "PK": enroll_token_pk(hash_secret(token)),
            "SK": "META",
            "account_id": account_id,
            "used": False,
            "expires_at": int(time.time()) - 60,
        }
    )
    return token
