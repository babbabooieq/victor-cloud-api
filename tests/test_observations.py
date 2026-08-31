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


def test_upload_observations_succeeds_and_stores_them(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    collector_id, credential = enroll_collector(client, dynamodb_table, account_id)

    resp = client.post(
        "/v1/sessions/S-1/observations",
        json={
            "task_id": "task_1",
            "observations": [
                {"plugin": "service-inventory", "target": "10.10.10.25", "raw": {"port": 22}},
                {"plugin": "service-inventory", "target": "10.10.10.25", "raw": {"port": 80}},
            ],
        },
        headers={"Authorization": f"Bearer {credential}"},
    )

    assert resp.status_code == 202
    assert resp.json()["accepted"] == 2

    stored = dynamodb_table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": db.account_pk(account_id), ":prefix": "OBS#S-1#"},
    )["Items"]
    assert len(stored) == 2
    assert {i["raw"]["port"] for i in stored} == {22, 80}


def test_upload_observations_marks_the_task_completed(client, dynamodb_table):
    account_id = make_account(dynamodb_table)
    collector_id, credential = enroll_collector(client, dynamodb_table, account_id)

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

    client.post(
        "/v1/sessions/S-1/observations",
        json={
            "task_id": "task_1",
            "observations": [{"plugin": "service-inventory", "target": "10.10.10.25", "raw": {}}],
        },
        headers={"Authorization": f"Bearer {credential}"},
    )

    task = dynamodb_table.get_item(
        Key={"PK": db.collector_pk(collector_id), "SK": db.task_sk("task_1")}
    )["Item"]
    assert task["status"] == "completed"


def test_upload_observations_rejects_invalid_credential(client, dynamodb_table):
    resp = client.post(
        "/v1/sessions/S-1/observations",
        json={"task_id": "task_1", "observations": []},
        headers={"Authorization": "Bearer not-real"},
    )
    assert resp.status_code == 401


def test_upload_observations_survives_a_nonexistent_task_id(client, dynamodb_table):
    # Ad-hoc testing (no real task ever assigned) shouldn't fail the upload -
    # the observations are the important part.
    account_id = make_account(dynamodb_table)
    _, credential = enroll_collector(client, dynamodb_table, account_id)

    resp = client.post(
        "/v1/sessions/S-1/observations",
        json={
            "task_id": "task_does_not_exist",
            "observations": [{"plugin": "service-inventory", "target": "10.10.10.25", "raw": {}}],
        },
        headers={"Authorization": f"Bearer {credential}"},
    )

    assert resp.status_code == 202
    assert resp.json()["accepted"] == 1
