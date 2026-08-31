from __future__ import annotations

from victor_cloud_api.auth import consume_enrollment_token
from tests.conftest import make_account


def headers(account_id: str) -> dict:
    return {"X-Account-Id": account_id}


def test_create_enrollment_token_returns_a_usable_token(client, dynamodb_table):
    account_id = make_account(dynamodb_table)

    resp = client.post("/v1/collectors/enrollment-tokens", headers=headers(account_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["token"].startswith("vre_enroll_")
    assert body["expires_in_seconds"] == 900


def test_issued_token_actually_works_for_enroll(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    token = client.post("/v1/collectors/enrollment-tokens", headers=headers(account_id)).json()[
        "token"
    ]

    resp = client.post(
        "/v1/collectors/enroll",
        json={"hostname": "h", "os": "linux", "arch": "x86_64", "version": "0.1.0"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["control_plane_id"] == account_id


def test_token_is_scoped_to_the_requesting_account(client, dynamodb_table):
    account_a = make_account(dynamodb_table, account_id="acct_a")
    make_account(dynamodb_table, account_id="acct_b")

    token = client.post("/v1/collectors/enrollment-tokens", headers=headers(account_a)).json()[
        "token"
    ]

    resolved_account = consume_enrollment_token(dynamodb_table, token)
    assert resolved_account == account_a
