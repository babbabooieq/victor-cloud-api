"""Human-user auth for the web app's CRUD routes.

PLACEHOLDER: trusts a caller-supplied header with zero verification.
This is deliberately isolated to one function - cloud-api-design.md §3
proposes Cognito for real auth but leaves it an open, unconfirmed
question (§8). Business-logic routes depend on get_current_account_id()
returning a verified account_id, not on *how* it's verified - so wiring
in Cognito (a JWT claim via an API Gateway authorizer, most likely)
later means changing this one function, not every route that uses it.

Do not deploy this behind a public endpoint before that swap happens -
it currently lets any caller claim to be any account.
"""

from __future__ import annotations

from fastapi import Header


def get_current_account_id(x_account_id: str = Header(..., alias="X-Account-Id")) -> str:
    return x_account_id
