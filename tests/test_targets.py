from __future__ import annotations

from tests.conftest import make_account

CREATE_TARGET = {
    "host": "lab-web-01",
    "ip": "10.10.10.25",
    "scope": "Internal Lab",
    "auth": "Confirmed",
    "services": 5,
    "os": "Debian 12",
}


def headers(account_id: str) -> dict:
    return {"X-Account-Id": account_id}


def test_create_and_list_target(client, dynamodb_table):
    account_id = make_account(dynamodb_table)

    create_resp = client.post("/v1/targets", json=CREATE_TARGET, headers=headers(account_id))
    assert create_resp.status_code == 200
    assert create_resp.json()["host"] == "lab-web-01"
    assert create_resp.json()["services"] == 5

    list_resp = client.get("/v1/targets", headers=headers(account_id))
    assert len(list_resp.json()) == 1


def test_targets_default_services_and_os_when_omitted(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    resp = client.post(
        "/v1/targets",
        json={"host": "new-host", "ip": "10.0.0.5", "scope": "Owned Asset", "auth": "Confirmed"},
        headers=headers(account_id),
    )
    assert resp.json()["services"] == 0
    assert resp.json()["os"] == "Unknown"
