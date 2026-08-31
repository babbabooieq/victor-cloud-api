"""Direct tests for get_current_account_id - the fail-closed JWT/local-dev
split is security-critical enough to deserve its own coverage, not just
whatever the CRUD route tests happen to exercise via the header path.
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from victor_cloud_api.deps import get_current_account_id


def _request(scope_extra: dict | None = None, headers: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        **(scope_extra or {}),
    }
    return Request(scope)


def test_extracts_sub_from_api_gateway_jwt_claims():
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "cognito-user-123"}}}}
    }
    request = _request({"aws.event": event})

    assert get_current_account_id(request) == "cognito-user-123"


def test_behind_api_gateway_with_missing_claims_is_401_not_a_fallback():
    # A real deployment scenario: authorizer attached but somehow no sub
    # claim (misconfigured pool, wrong scope, etc). Must fail closed, not
    # fall back to trusting a header - there IS no header trust once
    # aws.event is present.
    event = {"requestContext": {"authorizer": {"jwt": {"claims": {}}}}}
    request = _request({"aws.event": event}, headers={"X-Account-Id": "attacker-supplied"})

    with pytest.raises(HTTPException) as exc_info:
        get_current_account_id(request)
    assert exc_info.value.status_code == 401


def test_local_dev_fallback_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("VICTOR_ALLOW_INSECURE_LOCAL_AUTH", raising=False)
    request = _request(headers={"X-Account-Id": "acct_test"})

    with pytest.raises(HTTPException) as exc_info:
        get_current_account_id(request)
    assert exc_info.value.status_code == 500


def test_local_dev_fallback_works_when_opted_in(monkeypatch):
    monkeypatch.setenv("VICTOR_ALLOW_INSECURE_LOCAL_AUTH", "1")
    request = _request(headers={"X-Account-Id": "acct_test"})

    assert get_current_account_id(request) == "acct_test"


def test_local_dev_fallback_still_requires_the_header(monkeypatch):
    monkeypatch.setenv("VICTOR_ALLOW_INSECURE_LOCAL_AUTH", "1")
    request = _request()

    with pytest.raises(HTTPException) as exc_info:
        get_current_account_id(request)
    assert exc_info.value.status_code == 401
