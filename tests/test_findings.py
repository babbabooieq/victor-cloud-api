from __future__ import annotations

from tests.conftest import make_account

CREATE_BODY = {
    "session_id": "S-1",
    "title": "Outdated SSH Service",
    "severity": "High",
    "confidence": 91,
    "asset": "10.10.10.25:22",
    "evidence": "Banner: OpenSSH_7.4",
    "remediation": "Upgrade OpenSSH.",
    "tags": ["ssh"],
}


def headers(account_id: str) -> dict:
    return {"X-Account-Id": account_id}


def test_create_and_list_finding(client, dynamodb_table):
    account_id = make_account(dynamodb_table)

    create_resp = client.post("/v1/findings", json=CREATE_BODY, headers=headers(account_id))
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["title"] == "Outdated SSH Service"
    assert created["severity"] == "High"
    assert created["reviewed"] is False
    assert created["owner"] is None

    list_resp = client.get("/v1/findings", headers=headers(account_id))
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["id"] == created["id"]


def test_findings_are_isolated_per_account(client, dynamodb_table):
    account_a = make_account(dynamodb_table, account_id="acct_a")
    account_b = make_account(dynamodb_table, account_id="acct_b")

    client.post("/v1/findings", json=CREATE_BODY, headers=headers(account_a))

    assert len(client.get("/v1/findings", headers=headers(account_a)).json()) == 1
    assert len(client.get("/v1/findings", headers=headers(account_b)).json()) == 0


def test_patch_finding_severity_and_owner(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    finding_id = client.post("/v1/findings", json=CREATE_BODY, headers=headers(account_id)).json()[
        "id"
    ]

    resp = client.patch(
        f"/v1/findings/{finding_id}",
        json={"severity": "Critical", "owner": "a.surujnarine"},
        headers=headers(account_id),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["severity"] == "Critical"
    assert body["owner"] == "a.surujnarine"
    # Untouched fields survive a partial patch.
    assert body["title"] == "Outdated SSH Service"


def test_patch_finding_reviewed_flag(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    finding_id = client.post("/v1/findings", json=CREATE_BODY, headers=headers(account_id)).json()[
        "id"
    ]

    resp = client.patch(
        f"/v1/findings/{finding_id}", json={"reviewed": True}, headers=headers(account_id)
    )

    assert resp.json()["reviewed"] is True


def test_patch_nonexistent_finding_returns_404(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    resp = client.patch(
        "/v1/findings/does_not_exist", json={"reviewed": True}, headers=headers(account_id)
    )
    assert resp.status_code == 404


def test_cannot_patch_another_accounts_finding(client, dynamodb_table):
    account_a = make_account(dynamodb_table, account_id="acct_a")
    account_b = make_account(dynamodb_table, account_id="acct_b")
    finding_id = client.post("/v1/findings", json=CREATE_BODY, headers=headers(account_a)).json()[
        "id"
    ]

    resp = client.patch(
        f"/v1/findings/{finding_id}", json={"reviewed": True}, headers=headers(account_b)
    )

    assert resp.status_code == 404
