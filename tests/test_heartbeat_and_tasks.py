from __future__ import annotations

from victor_cloud_api import db
from victor_cloud_api.auth import issue_enrollment_token
from tests.conftest import make_account

ENROLL_BODY = {"hostname": "collector-01", "os": "linux", "arch": "x86_64", "version": "0.1.0"}


def enroll_collector(client, table, account_id: str) -> tuple[str, str]:
    """Returns (collector_id, credential)."""
    token = issue_enrollment_token(table, account_id)
    resp = client.post(
        "/v1/collectors/enroll", json=ENROLL_BODY, headers={"Authorization": f"Bearer {token}"}
    )
    body = resp.json()
    return body["collector_id"], body["credential"]


def auth_headers(credential: str) -> dict:
    return {"Authorization": f"Bearer {credential}"}


# --- heartbeat -------------------------------------------------------------


def test_heartbeat_succeeds_with_valid_credential(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    _, credential = enroll_collector(client, dynamodb_table, account_id)

    resp = client.post(
        "/v1/collectors/heartbeat",
        json={"version": "0.1.0", "status": "idle"},
        headers=auth_headers(credential),
    )

    assert resp.status_code == 200
    assert resp.json()["min_supported_version"] == "0.2.0"


def test_heartbeat_updates_the_collector_item(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    collector_id, credential = enroll_collector(client, dynamodb_table, account_id)

    client.post(
        "/v1/collectors/heartbeat",
        json={"version": "0.2.0", "status": "running"},
        headers=auth_headers(credential),
    )

    item = dynamodb_table.get_item(
        Key={"PK": db.account_pk(account_id), "SK": db.collector_account_sk(collector_id)}
    )["Item"]
    assert item["status"] == "running"
    assert item["version"] == "0.2.0"
    assert item["last_heartbeat"] is not None


def test_heartbeat_rejects_invalid_credential(client, dynamodb_table):
    resp = client.post(
        "/v1/collectors/heartbeat",
        json={"version": "0.1.0", "status": "idle"},
        headers=auth_headers("vre_col_not_real"),
    )
    assert resp.status_code == 401


# --- tasks -------------------------------------------------------------


def test_tasks_empty_when_none_assigned(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    _, credential = enroll_collector(client, dynamodb_table, account_id)

    resp = client.get("/v1/collectors/tasks", headers=auth_headers(credential))

    assert resp.status_code == 200
    assert resp.json()["tasks"] == []


def test_tasks_returns_only_pending_tasks_for_this_collector(client, dynamodb_table):
    # team tier: two collectors on one account needs an unlimited-collector
    # tier, not the community default (capped at 1).
    account_id = make_account(dynamodb_table, tier="team")
    collector_id, credential = enroll_collector(client, dynamodb_table, account_id)
    other_collector_id, _ = enroll_collector(client, dynamodb_table, account_id)

    # Pending task for our collector - should show up.
    dynamodb_table.put_item(
        Item={
            "PK": db.collector_pk(collector_id),
            "SK": db.task_sk("task_1"),
            "task_id": "task_1",
            "account_id": account_id,
            "collector_id": collector_id,
            "session_id": "S-1",
            "plugin": "service-inventory",
            "target": "10.10.10.25",
            "safe_mode": True,
            "status": "pending",
        }
    )
    # Completed task for our collector - should NOT show up.
    dynamodb_table.put_item(
        Item={
            "PK": db.collector_pk(collector_id),
            "SK": db.task_sk("task_2"),
            "task_id": "task_2",
            "account_id": account_id,
            "collector_id": collector_id,
            "session_id": "S-1",
            "plugin": "tls-review",
            "target": "10.10.10.25",
            "safe_mode": True,
            "status": "completed",
        }
    )
    # Pending task for a DIFFERENT collector - should NOT show up.
    dynamodb_table.put_item(
        Item={
            "PK": db.collector_pk(other_collector_id),
            "SK": db.task_sk("task_3"),
            "task_id": "task_3",
            "account_id": account_id,
            "collector_id": other_collector_id,
            "session_id": "S-1",
            "plugin": "service-inventory",
            "target": "10.10.10.30",
            "safe_mode": True,
            "status": "pending",
        }
    )

    resp = client.get("/v1/collectors/tasks", headers=auth_headers(credential))

    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "task_1"
    assert tasks[0]["plugin"] == "service-inventory"
