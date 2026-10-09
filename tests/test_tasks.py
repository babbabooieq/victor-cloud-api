from __future__ import annotations

from victor_cloud_api import db
from victor_cloud_api.auth import issue_enrollment_token
from tests.conftest import make_account

ENROLL_BODY = {"hostname": "collector-01", "os": "linux", "arch": "x86_64", "version": "0.1.0"}


def enroll_collector(client, table, account_id: str) -> tuple[str, str]:
    token = issue_enrollment_token(table, account_id)
    resp = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {token}"}
    )
    body = resp.json()
    return body["collector_id"], body["credential"]


def account_headers(account_id: str) -> dict:
    return {"X-Account-Id": account_id}


def create_session(client, account_id: str) -> str:
    resp = client.post(
        "/v1/sessions",
        json={"name": "Test session", "target": "10.10.10.25", "scope": "Authorized",
              "authorization": {"reference": "Authorized", "target": "10.10.10.25", "allowed_plugins": ["service-inventory", "tls-review", "http-header-audit"], "expires_at": "2099-01-01T00:00:00Z"}},
        headers=account_headers(account_id),
    )
    return resp.json()["id"]


def test_create_task_auto_assigns_the_only_collector(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    collector_id, _ = enroll_collector(client, dynamodb_table, account_id)
    session_id = create_session(client, account_id)

    resp = client.post(
        f"/v1/sessions/{session_id}/tasks",
        json={"plugin": "service-inventory", "target": "10.10.10.25"},
        headers=account_headers(account_id),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["collector_id"] == collector_id
    assert body["session_id"] == session_id
    assert body["plugin"] == "service-inventory"
    assert body["status"] == "pending"


def test_created_task_is_visible_to_the_collectors_poll(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    _, credential = enroll_collector(client, dynamodb_table, account_id)
    session_id = create_session(client, account_id)

    client.post(
        f"/v1/sessions/{session_id}/tasks",
        json={"plugin": "tls-review", "target": "10.10.10.25"},
        headers=account_headers(account_id),
    )

    resp = client.get(
        "/v1/collectors/tasks", headers={"Authorization": f"Bearer {credential}"}
    )
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["plugin"] == "tls-review"
    assert tasks[0]["session_id"] == session_id


def test_create_task_404s_for_unknown_session(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    enroll_collector(client, dynamodb_table, account_id)

    resp = client.post(
        "/v1/sessions/S-does-not-exist/tasks",
        json={"plugin": "service-inventory", "target": "10.10.10.25"},
        headers=account_headers(account_id),
    )
    assert resp.status_code == 404


def test_create_task_409s_when_no_collector_enrolled(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    session_id = create_session(client, account_id)

    resp = client.post(
        f"/v1/sessions/{session_id}/tasks",
        json={"plugin": "service-inventory", "target": "10.10.10.25"},
        headers=account_headers(account_id),
    )
    assert resp.status_code == 409
    assert "No collector enrolled" in resp.json()["detail"]


def test_create_task_409s_when_multiple_collectors_enrolled(client, dynamodb_table):
    account_id = make_account(dynamodb_table, tier="team")
    enroll_collector(client, dynamodb_table, account_id)
    enroll_collector(client, dynamodb_table, account_id)
    session_id = create_session(client, account_id)

    resp = client.post(
        f"/v1/sessions/{session_id}/tasks",
        json={"plugin": "service-inventory", "target": "10.10.10.25"},
        headers=account_headers(account_id),
    )
    assert resp.status_code == 409
    assert "Multiple collectors" in resp.json()["detail"]


def test_create_task_is_isolated_to_the_requesting_account(client, dynamodb_table):
    account_a = make_account(dynamodb_table, account_id="acct_a")
    account_b = make_account(dynamodb_table, account_id="acct_b")
    enroll_collector(client, dynamodb_table, account_a)
    session_id_a = create_session(client, account_a)

    # Account B (which has no session with this id) must not be able to
    # create a task against account A's session.
    resp = client.post(
        f"/v1/sessions/{session_id_a}/tasks",
        json={"plugin": "service-inventory", "target": "10.10.10.25"},
        headers=account_headers(account_b),
    )
    assert resp.status_code == 404


def test_gsi3_reflects_the_new_task_for_account_side_visibility(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    enroll_collector(client, dynamodb_table, account_id)
    session_id = create_session(client, account_id)

    client.post(
        f"/v1/sessions/{session_id}/tasks",
        json={"plugin": "service-inventory", "target": "10.10.10.25"},
        headers=account_headers(account_id),
    )

    resp = dynamodb_table.query(
        IndexName="GSI3",
        KeyConditionExpression="gsi3pk = :pk",
        ExpressionAttributeValues={":pk": db.account_pk(account_id)},
    )
    assert resp["Count"] == 1
