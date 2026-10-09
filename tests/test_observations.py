from __future__ import annotations

from victor_cloud_api import db
from victor_cloud_api.auth import issue_enrollment_token
from tests.conftest import make_account
from victor_cloud_api.models import Session, Task


def test_duplicate_source_ids_are_rejected_before_writes(client, dynamodb_table):
    account = make_account(dynamodb_table)
    collector, credential = enroll_collector(client, dynamodb_table, account)
    assigned_session(dynamodb_table, account, collector)
    observation = {"id": "duplicate", "plugin": "service-inventory", "target": "10.10.10.25", "raw": {"port": 22}}
    response = client.post("/v1/sessions/S-1/observations", json={"task_id": "task_1", "observations": [observation, observation]}, headers={"Authorization": f"Bearer {credential}"})
    assert response.status_code == 422
    assert client.get("/v1/sessions/S-1/observations", headers={"X-Account-Id": account}).json() == []


def test_legacy_or_partial_evidence_is_not_finalized(client, dynamodb_table):
    account = make_account(dynamodb_table)
    collector, credential = enroll_collector(client, dynamodb_table, account)
    assigned_session(dynamodb_table, account, collector)
    dynamodb_table.update_item(Key={"PK": db.collector_pk(collector), "SK": db.task_sk("task_1")}, UpdateExpression="SET #status = :status", ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues={":status": "completed"})
    item = {"PK": db.account_pk(account), "SK": db.observation_sk("S-1", "legacy"), "id": "legacy", "session_id": "S-1", "task_id": "task_1", "collector_id": collector, "plugin": "service-inventory", "target": "10.10.10.25", "raw": {"port": 22}}
    dynamodb_table.put_item(Item=item)
    assert not client.get("/v1/sessions/S-1/observations", headers={"X-Account-Id": account}).json()[0]["finalized"]
    item["batch_fingerprint"] = "partial"
    dynamodb_table.put_item(Item=item)
    assert not client.get("/v1/sessions/S-1/observations", headers={"X-Account-Id": account}).json()[0]["finalized"]

ENROLL_BODY = {"hostname": "collector-01", "os": "linux", "arch": "x86_64", "version": "0.1.0"}


def assigned_session(table, account_id, collector_id):
    session = Session(id="S-1", account_id=account_id, name="Test", target="10.10.10.25", scope="Owned lab")
    table.put_item(Item={"PK": db.account_pk(account_id), "SK": db.session_sk(session.id), **session.model_dump()})
    task = Task(task_id="task_1", account_id=account_id, collector_id=collector_id, session_id=session.id,
                plugin="service-inventory", target=session.target)
    table.put_item(Item={"PK": db.collector_pk(collector_id), "SK": db.task_sk(task.task_id), **task.model_dump()})


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
    assigned_session(dynamodb_table, account_id, collector_id)

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
    assigned_session(dynamodb_table, account_id, collector_id)

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


def test_upload_observations_rejects_a_nonexistent_task_id(client, dynamodb_table):
    # Missing attribution must fail closed, even for the right account.
    account_id = make_account(dynamodb_table)
    collector_id, credential = enroll_collector(client, dynamodb_table, account_id)
    assigned_session(dynamodb_table, account_id, collector_id)

    resp = client.post(
        "/v1/sessions/S-1/observations",
        json={
            "task_id": "task_does_not_exist",
            "observations": [{"plugin": "service-inventory", "target": "10.10.10.25", "raw": {}}],
        },
        headers={"Authorization": f"Bearer {credential}"},
    )

    assert resp.status_code == 404


def test_replay_keeps_evidence_immutable_and_preserves_timestamp(client, dynamodb_table):
    account = make_account(dynamodb_table)
    collector, credential = enroll_collector(client, dynamodb_table, account)
    assigned_session(dynamodb_table, account, collector)
    body = {"task_id": "task_1", "observations": [{"id": "source-1", "collected_at": "2026-10-09T12:00:00Z", "plugin": "service-inventory", "target": "HOST-redacted", "redacted": True, "raw": {"port": 22, "confidence": 0.75}}]}
    headers = {"Authorization": f"Bearer {credential}"}
    assert client.post("/v1/sessions/S-1/observations", json=body, headers=headers).status_code == 202
    assert client.post("/v1/sessions/S-1/observations", json=body, headers=headers).status_code == 202
    observations = client.get("/v1/sessions/S-1/observations", headers={"X-Account-Id": account}).json()
    assert len(observations) == 1
    assert observations[0]["collected_at"] == "2026-10-09T12:00:00Z"
    assert observations[0]["raw"]["confidence"] == 0.75
    body["observations"][0]["raw"]["port"] = 80
    assert client.post("/v1/sessions/S-1/observations", json=body, headers=headers).status_code == 409


def test_same_account_wrong_session_and_plugin_are_rejected(client, dynamodb_table):
    account = make_account(dynamodb_table)
    collector, credential = enroll_collector(client, dynamodb_table, account)
    assigned_session(dynamodb_table, account, collector)
    other = Session(id="S-other", account_id=account, name="Other", target="10.10.10.25", scope="Lab")
    dynamodb_table.put_item(Item={"PK": db.account_pk(account), "SK": db.session_sk(other.id), **other.model_dump()})
    body = {"task_id": "task_1", "observations": [{"plugin": "tls-review", "target": "redacted", "raw": {}}]}
    headers = {"Authorization": f"Bearer {credential}"}
    assert client.post("/v1/sessions/S-other/observations", json=body, headers=headers).status_code == 422
    assert client.post("/v1/sessions/S-1/observations", json=body, headers=headers).status_code == 422


def test_other_account_cannot_read_or_upload_session_evidence(client, dynamodb_table):
    account_a = make_account(dynamodb_table, "acct_a")
    collector_a, _ = enroll_collector(client, dynamodb_table, account_a)
    assigned_session(dynamodb_table, account_a, collector_a)
    account_b = make_account(dynamodb_table, "acct_b")
    _, credential_b = enroll_collector(client, dynamodb_table, account_b)
    assert client.get("/v1/sessions/S-1/observations", headers={"X-Account-Id": account_b}).status_code == 404
    assert client.post("/v1/sessions/S-1/observations", json={"task_id": "task_1", "observations": []}, headers={"Authorization": f"Bearer {credential_b}"}).status_code == 404
