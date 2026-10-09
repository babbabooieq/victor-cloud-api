"""Optional cross-repo integration: pip install -e ../victor-collector first.

Uses the real collector loop and redaction against this API's in-memory DynamoDB.
Only plugin I/O is replaced; no live scan, cloud resource or user credential.
"""
import json
from datetime import datetime

import httpx
import pytest

pytest.importorskip("victor_collector", reason="Install sibling collector for cross-repo protocol checks")

from victor_collector import daemon, policy, redaction
from victor_collector.models import CollectorCredentials, RawObservation
from tests.conftest import make_account
from tests.test_tasks import enroll_collector, create_session, account_headers


class ApiTransport(httpx.BaseTransport):
    def __init__(self, api_client):
        self.api_client = api_client

    def handle_request(self, request):
        response = self.api_client.request(request.method, str(request.url), content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers=response.headers, request=request)


def test_real_collector_loop_claims_redacts_and_finalizes_evidence(client, dynamodb_table, monkeypatch, tmp_path):
    account = make_account(dynamodb_table)
    collector, credential = enroll_collector(client, dynamodb_table, account)
    session = create_session(client, account)
    task = client.post(f"/v1/sessions/{session}/tasks", json={"plugin": "http-header-audit", "target": "10.10.10.25"}, headers=account_headers(account)).json()
    captured = []
    monkeypatch.setattr(policy, "POLICY_PATH", tmp_path / "scope.json")
    policy.authorize("10.10.10.25", "2099-01-01T00:00:00Z", ["http-header-audit"])

    class Plugin:
        safe_mode = True

        def run(self, target, config):
            observation = RawObservation(plugin="http-header-audit", target="10.10.10.25", raw={"url": "https://10.10.10.25/", "status_code": 200, "missing_headers": ["Content-Security-Policy"], "present_headers": {"X-Frame-Options": "DENY"}})
            captured.append(observation)
            return [observation]

    monkeypatch.setattr(daemon.plugin_registry, "get", lambda name: Plugin())
    real_redact = redaction.redact_observation
    monkeypatch.setattr(daemon.redaction, "redact_observation", lambda observation, *, session_id: real_redact(observation, session_id=session_id, db_path=tmp_path / "redaction.db"))
    creds = CollectorCredentials(control_plane="http://testserver", collector_id=collector, credential=credential, account_id=account, hostname="fixture")
    with httpx.Client(transport=ApiTransport(client)) as sync_client:
        daemon.run_once(creds, sync_client, 0, now=100)
    tasks = client.get(f"/v1/sessions/{session}/tasks", headers=account_headers(account)).json()
    observations = client.get(f"/v1/sessions/{session}/observations", headers=account_headers(account)).json()
    assert tasks[0]["task_id"] == task["task_id"]
    assert tasks[0]["status"] == "completed" and tasks[0]["attempts"] == 1
    assert len(observations) == 1 and observations[0]["finalized"]
    assert observations[0]["raw"]["status_code"] == 200
    assert datetime.fromisoformat(observations[0]["collected_at"]) == captured[0].collected_at
    assert "10.10.10.25" not in json.dumps(observations)
    assert client.get("/v1/collectors/tasks", headers={"Authorization": f"Bearer {credential}"}).json()["tasks"] == []
