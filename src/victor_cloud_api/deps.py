"""Human-user auth for the web app's CRUD routes.

Real Cognito JWT auth, not a placeholder anymore. API Gateway's JWT
authorizer validates the token's signature/expiry *before* Lambda ever
runs - Lambda never sees a request with a bad token, only the resulting
claims. Mangum exposes the raw Lambda event at request.scope["aws.event"],
which is where those claims live.

account_id == the Cognito user's `sub` (their UUID). No separate mapping
table for v0.1 - one Cognito user is one Account. Multi-user
orgs/MSP-tier are a real future need (KILLER_FEATURES.md §4.7) but
nothing in the product needs it yet, so it's not built ahead of time.

Fails closed: if this is running behind API Gateway (real Lambda) and the
JWT claims are missing, that's a 401, never a silent fallback to
trusting a client-supplied header. The local-dev fallback below only
activates with an explicit opt-in env var that would never be set in the
real Lambda's environment - so a misconfigured authorizer in production
fails loudly, it doesn't quietly become insecure.
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request

_INSECURE_LOCAL_AUTH_FLAG = "VICTOR_ALLOW_INSECURE_LOCAL_AUTH"


def get_current_account_id(request: Request) -> str:
    event = request.scope.get("aws.event")

    if event is not None:
        # Behind API Gateway - only trust what the JWT authorizer already
        # validated. Never read a client header here.
        claims = (
            event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        )
        sub = claims.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Missing or invalid authentication")
        return sub

    # Not behind API Gateway at all (local dev / tests). Requires an
    # explicit opt-in so this path can never be silently active anywhere
    # that matters.
    if os.environ.get(_INSECURE_LOCAL_AUTH_FLAG) != "1":
        raise HTTPException(
            status_code=500,
            detail=(
                f"No API Gateway JWT context found and {_INSECURE_LOCAL_AUTH_FLAG} isn't set - "
                "refusing to guess at an account_id. Set it to '1' for local dev only."
            ),
        )
    account_id = request.headers.get("X-Account-Id")
    if not account_id:
        raise HTTPException(status_code=401, detail="X-Account-Id header required in local dev mode")
    return account_id
