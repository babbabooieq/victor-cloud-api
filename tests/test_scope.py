from victor_cloud_api import db
from victor_cloud_api.target_validation import validate_target
from tests.test_execution import setup_task
from tests.test_tasks import account_headers
import pytest


def test_authorization_expiry_and_plugin_restrictions(client, dynamodb_table):
    account, collector, session, task, headers = setup_task(client, dynamodb_table)
    key = {"PK": db.account_pk(account), "SK": db.session_sk(session)}
    record = dynamodb_table.get_item(Key=key)["Item"]["authorization"]
    record["allowed_plugins"] = ["http-header-audit"]
    dynamodb_table.update_item(Key=key, UpdateExpression="SET #auth = :auth", ExpressionAttributeNames={"#auth": "authorization"}, ExpressionAttributeValues={":auth": record})
    body = {"plugin": "tls-review", "target": "10.10.10.25"}
    assert client.post(f"/v1/sessions/{session}/tasks", json=body, headers=account_headers(account)).status_code == 422
    record["expires_at"] = "2020-01-01T00:00:00Z"
    dynamodb_table.update_item(Key=key, UpdateExpression="SET #auth = :auth", ExpressionAttributeNames={"#auth": "authorization"}, ExpressionAttributeValues={":auth": record})
    body["plugin"] = "http-header-audit"
    assert client.post(f"/v1/sessions/{session}/tasks", json=body, headers=account_headers(account)).status_code == 422


def test_expired_queued_authorization_is_not_claimable(client, dynamodb_table):
    account, collector, session, task, headers = setup_task(client, dynamodb_table)
    dynamodb_table.update_item(Key={"PK": db.collector_pk(collector), "SK": db.task_sk(task)}, UpdateExpression="SET authorization_expires_at = :past", ExpressionAttributeValues={":past": "2020-01-01T00:00:00Z"})
    assert client.post(f"/v1/collectors/tasks/{task}/claim", json={}, headers=headers).status_code == 409
    assert client.get("/v1/collectors/tasks", headers=headers).json()["tasks"] == []
    assert client.get(f"/v1/sessions/{session}/tasks", headers=account_headers(account)).json()[0]["status"] == "failed"


@pytest.mark.parametrize("target", ["10.0.0.1-10", "10.0.0.0/24", "10.0.0.1,10.0.0.2", "10.0.0.*", "--script=all", "https://user:secret@example.com", "example.com other.com"])
def test_target_lists_ranges_and_credentials_are_refused(target):
    with pytest.raises(ValueError):
        validate_target(target)


@pytest.mark.parametrize("target", ["example.com", "10.10.10.25", "::1", "https://example.com:8443/path"])
def test_exact_hosts_and_http_urls_are_supported(target):
    assert validate_target(target) == target
