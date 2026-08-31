from __future__ import annotations

from victor_cloud_api import db
from victor_cloud_api.auth import issue_enrollment_token, resolve_collector_by_credential
from tests.conftest import make_account, make_expired_token

ENROLL_BODY = {"hostname": "collector-01", "os": "linux", "arch": "x86_64", "version": "0.1.0"}


def test_enroll_succeeds_with_valid_token(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    token = issue_enrollment_token(dynamodb_table, account_id)

    resp = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["control_plane_id"] == account_id
    assert body["collector_id"].startswith("col_")
    assert body["credential"].startswith("vre_col_")


def test_enrolled_credential_resolves_back_to_the_collector(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    token = issue_enrollment_token(dynamodb_table, account_id)
    resp = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {token}"}
    )
    credential = resp.json()["credential"]

    resolved = resolve_collector_by_credential(dynamodb_table, credential)

    assert resolved is not None
    assert resolved["account_id"] == account_id
    assert resolved["hostname"] == "collector-01"


def test_token_cannot_be_reused(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    token = issue_enrollment_token(dynamodb_table, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/v1/collectors/enroll", json=ENROLL_BODY, headers=headers)
    second = client.post("/v1/collectors/enroll", json=ENROLL_BODY, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 401


def test_expired_token_is_rejected(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    token = make_expired_token(dynamodb_table, account_id)

    resp = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 401


def test_garbage_token_is_rejected(client, dynamodb_table):
    resp = client.post(
        "/v1/collectors/enroll",
        json=ENROLL_BODY,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_missing_bearer_prefix_is_rejected(client, dynamodb_table):
    resp = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": "just-a-token"}
    )
    assert resp.status_code == 401


def test_community_tier_capped_at_one_collector(client, dynamodb_table):
    account_id = make_account(dynamodb_table, tier="community")

    first_token = issue_enrollment_token(dynamodb_table, account_id)
    first = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {first_token}"}
    )
    assert first.status_code == 200

    second_token = issue_enrollment_token(dynamodb_table, account_id)
    second = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {second_token}"}
    )
    assert second.status_code == 403


def test_team_tier_has_no_collector_cap(client, dynamodb_table):
    account_id = make_account(dynamodb_table, tier="team")

    for _ in range(5):
        token = issue_enrollment_token(dynamodb_table, account_id)
        resp = client.post(
            "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200


def test_enroll_with_missing_account_returns_server_error(client, dynamodb_table):
    # A token whose account was deleted between issuance and use - data
    # integrity problem, should surface as 500, not a silent 401.
    from victor_cloud_api.auth import enroll_token_pk, hash_secret
    import time

    token = "vre_enroll_orphaned"
    dynamodb_table.put_item(
        Item={
            "PK": enroll_token_pk(hash_secret(token)),
            "SK": "META",
            "account_id": "acct_does_not_exist",
            "used": False,
            "expires_at": int(time.time()) + 900,
        }
    )

    resp = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 500
