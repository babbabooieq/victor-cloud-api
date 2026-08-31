from __future__ import annotations

from tests.conftest import make_account

CREATE_SESSION = {"name": "Lab Web Perimeter Review", "target": "10.10.10.25", "scope": "Internal Lab"}
CREATE_FINDING = {
    "title": "Outdated SSH Service",
    "severity": "High",
    "confidence": 91,
    "asset": "10.10.10.25:22",
    "evidence": "Banner: OpenSSH_7.4",
    "remediation": "Upgrade OpenSSH.",
    "tags": [],
}


def headers(account_id: str) -> dict:
    return {"X-Account-Id": account_id}


def test_create_and_list_session(client, dynamodb_table):
    account_id = make_account(dynamodb_table)

    create_resp = client.post("/v1/sessions", json=CREATE_SESSION, headers=headers(account_id))
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["name"] == "Lab Web Perimeter Review"
    assert created["status"] == "Active"
    assert created["findings"] == 0

    list_resp = client.get("/v1/sessions", headers=headers(account_id))
    assert len(list_resp.json()) == 1


def test_session_findings_count_reflects_real_findings(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    session_id = client.post(
        "/v1/sessions", json=CREATE_SESSION, headers=headers(account_id)
    ).json()["id"]

    client.post(
        "/v1/findings", json={**CREATE_FINDING, "session_id": session_id}, headers=headers(account_id)
    )
    client.post(
        "/v1/findings", json={**CREATE_FINDING, "session_id": session_id}, headers=headers(account_id)
    )
    # A finding under a DIFFERENT session shouldn't count toward this one.
    client.post(
        "/v1/findings", json={**CREATE_FINDING, "session_id": "S-other"}, headers=headers(account_id)
    )

    sessions = client.get("/v1/sessions", headers=headers(account_id)).json()
    assert sessions[0]["findings"] == 2


def test_patch_session_status(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    session_id = client.post(
        "/v1/sessions", json=CREATE_SESSION, headers=headers(account_id)
    ).json()["id"]

    resp = client.patch(
        f"/v1/sessions/{session_id}", json={"status": "Archived"}, headers=headers(account_id)
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "Archived"


def test_patch_nonexistent_session_returns_404(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    resp = client.patch(
        "/v1/sessions/does_not_exist", json={"status": "Paused"}, headers=headers(account_id)
    )
    assert resp.status_code == 404
