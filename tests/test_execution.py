import time

from victor_cloud_api import db
from tests.conftest import make_account
from tests.test_tasks import enroll_collector, create_session, account_headers


def setup_task(client, table):
    account = make_account(table)
    collector, credential = enroll_collector(client, table, account)
    session = create_session(client, account)
    task = client.post(f"/v1/sessions/{session}/tasks", json={"plugin": "tls-review", "target": "10.10.10.25"}, headers=account_headers(account)).json()
    return account, collector, session, task["task_id"], {"Authorization": f"Bearer {credential}"}


def test_claim_excludes_other_workers_and_empty_outcome_stops_repolling(client, dynamodb_table):
    account, collector, session, task_id, headers = setup_task(client, dynamodb_table)
    claim = client.post(f"/v1/collectors/tasks/{task_id}/claim", json={}, headers=headers)
    assert claim.status_code == 200
    assert client.post(f"/v1/collectors/tasks/{task_id}/claim", json={}, headers=headers).status_code == 409
    assert client.get("/v1/collectors/tasks", headers=headers).json()["tasks"] == []
    assert client.post(f"/v1/collectors/tasks/{task_id}/result", json={"status": "empty", "lease_token": "wrong"}, headers=headers).status_code == 409
    assert client.post(f"/v1/collectors/tasks/{task_id}/result", json={"status": "empty", "lease_token": claim.json()["lease_token"]}, headers=headers).status_code == 200
    task = client.get(f"/v1/sessions/{session}/tasks", headers=account_headers(account)).json()[0]
    assert task["status"] == "empty"
    assert task["attempts"] == 1
    assert task["finished_at"]
    assert client.get("/v1/collectors/tasks", headers=headers).json()["tasks"] == []


def test_expired_lease_is_reclaimed_and_old_worker_cannot_upload(client, dynamodb_table):
    account, collector, session, task_id, headers = setup_task(client, dynamodb_table)
    first = client.post(f"/v1/collectors/tasks/{task_id}/claim", json={}, headers=headers).json()
    dynamodb_table.update_item(Key={"PK": db.collector_pk(collector), "SK": db.task_sk(task_id)}, UpdateExpression="SET lease_until = :past", ExpressionAttributeValues={":past": int(time.time()) - 1})
    assert client.get("/v1/collectors/tasks", headers=headers).json()["tasks"][0]["claim_required"]
    second = client.post(f"/v1/collectors/tasks/{task_id}/claim", json={}, headers=headers).json()
    body = {"task_id": task_id, "lease_token": first["lease_token"], "observations": [{"plugin": "tls-review", "target": "HOST-redacted", "raw": {}}]}
    assert client.post(f"/v1/sessions/{session}/observations", json=body, headers=headers).status_code == 409
    body["lease_token"] = second["lease_token"]
    assert client.post(f"/v1/sessions/{session}/observations", json=body, headers=headers).status_code == 202


def test_exhausted_task_does_not_starve_next_task(client, dynamodb_table):
    account, collector, session, task_id, headers = setup_task(client, dynamodb_table)
    client.post(f"/v1/collectors/tasks/{task_id}/claim", json={}, headers=headers)
    dynamodb_table.update_item(Key={"PK": db.collector_pk(collector), "SK": db.task_sk(task_id)}, UpdateExpression="SET lease_until = :past, attempts = :max", ExpressionAttributeValues={":past": 0, ":max": 3})
    next_task = client.post(f"/v1/sessions/{session}/tasks", json={"plugin": "tls-review", "target": "10.10.10.25"}, headers=account_headers(account)).json()
    polled = client.get("/v1/collectors/tasks", headers=headers).json()["tasks"]
    assert [task["task_id"] for task in polled] == [next_task["task_id"]]
    tasks = client.get(f"/v1/sessions/{session}/tasks", headers=account_headers(account)).json()
    assert next(task for task in tasks if task["task_id"] == task_id)["status"] == "failed"


def test_task_reads_and_claims_are_isolated_and_collector_listing_has_no_secrets(client, dynamodb_table):
    account, collector, session, task_id, headers = setup_task(client, dynamodb_table)
    other_account = make_account(dynamodb_table, "other")
    _, other_credential = enroll_collector(client, dynamodb_table, other_account)
    assert client.get(f"/v1/sessions/{session}/tasks", headers=account_headers(other_account)).status_code == 404
    assert client.post(f"/v1/collectors/tasks/{task_id}/claim", json={}, headers={"Authorization": f"Bearer {other_credential}"}).status_code == 404
    listing = client.get("/v1/collectors", headers=account_headers(account)).json()
    assert len(listing) == 1
    assert "gsi2pk" not in listing[0] and "credential" not in listing[0]


def test_tasks_refuse_unknown_plugin_mismatched_target_or_inactive_session(client, dynamodb_table):
    account, collector, session, task_id, headers = setup_task(client, dynamodb_table)
    for body in [{"plugin": "unknown", "target": "10.10.10.25"}, {"plugin": "tls-review", "target": "10.10.10.26"}, {"plugin": "service-inventory", "target": "--script=unsafe"}, {"plugin": "tls-review", "target": "10.10.10.25", "safe_mode": False}]:
        assert client.post(f"/v1/sessions/{session}/tasks", json=body, headers=account_headers(account)).status_code == 422
    client.patch(f"/v1/sessions/{session}", json={"status": "Paused"}, headers=account_headers(account))
    assert client.post(f"/v1/sessions/{session}/tasks", json={"plugin": "tls-review", "target": "10.10.10.25"}, headers=account_headers(account)).status_code == 409
