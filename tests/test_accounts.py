from __future__ import annotations

from victor_cloud_api import db


def headers(account_id: str) -> dict:
    return {"X-Account-Id": account_id}


def test_ensure_account_creates_it_on_first_call(client, dynamodb_table):
    resp = client.post("/v1/accounts/me", headers=headers("new-cognito-sub"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == "new-cognito-sub"
    assert body["tier"] == "community"

    item = dynamodb_table.get_item(
        Key={"PK": db.account_pk("new-cognito-sub"), "SK": db.account_meta_sk()}
    )["Item"]
    assert item["tier"] == "community"


def test_ensure_account_is_idempotent_and_does_not_reset_tier(client, dynamodb_table):
    client.post("/v1/accounts/me", headers=headers("existing-sub"))

    # Simulate an upgraded account - calling ensure_account again must not
    # silently downgrade it back to "community".
    dynamodb_table.update_item(
        Key={"PK": db.account_pk("existing-sub"), "SK": db.account_meta_sk()},
        UpdateExpression="SET tier = :t",
        ExpressionAttributeValues={":t": "team"},
    )

    resp = client.post("/v1/accounts/me", headers=headers("existing-sub"))

    assert resp.status_code == 200
    assert resp.json()["tier"] == "team"
