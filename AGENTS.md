# Working in this repo

The design source of truth is
[`docs/cloud-api-design.md`](https://github.com/babbabooieq/victor-recon-console/blob/main/docs/cloud-api-design.md)
in **victor-recon-console**, not here — DynamoDB schema, auth model, full
API surface, deployment plan. If this repo and that doc disagree, that's
a bug in one of them (usually the doc, since it was written before this
existed — keep it updated when you change something here, several
things already got fixed this way: the missing `RawObservation` and
enrollment-token table rows, the auth placeholder note).

## Current execution/evidence contract

Read the sibling console's `docs/live-workspace-protocol.md` before changes.
Human reads (`GET collectors`, session tasks/observations) require Cognito claims;
collector claims/results/uploads use hashed bearer credentials. Preserve this distinction
in API Gateway routes. Claims are exclusive and leased; new tasks require claims,
active exact-target authorization and expiry/plugin restrictions. Never accept completed
evidence changes or duplicate source IDs. Partial/legacy uploads without a matching
nonempty completion fingerprint are not finalized. Numeric JSON must stay numeric.
Use paginated queries and explicit account/session/task/collector attribution checks.
New code is source-tested; do not infer production rollout from a commit or push.
The optional real collector transport integration requires the sibling collector installed
in the API test environment: `pip install -e ../victor-collector`.

## The one thing that actually matters: `deps.py`

`get_current_account_id()` is real Cognito auth now, deployed and
proven end to end. It extracts the `sub` claim from
`request.scope["aws.event"]` — the claim API Gateway's Cognito JWT
authorizer already validated before Lambda ever ran — and fails closed
(401) if that context exists but the claim doesn't. There is a
local-dev fallback (an `X-Account-Id` header) but it only activates
behind an explicit `VICTOR_ALLOW_INSECURE_LOCAL_AUTH=1` opt-in that
would never be set in the real Lambda's environment — a misconfigured
authorizer in production fails loudly (500, "no context found"), it
never silently becomes insecure. If you're touching auth, read
`test_deps.py` first — it tests this fail-closed behavior directly, not
just indirectly through whatever the CRUD tests happen to exercise.

Collector-facing routes (`enroll`, `heartbeat`, `tasks`,
`observations`) are not affected by this — they use
`resolve_collector_by_credential()` (a real, hashed bearer-token
lookup via GSI2), which is fully built and not a placeholder.

## Schema discipline

`db.table_schema()` is the single definition of the table + 3 GSIs.
Both the real AWS table and every test's in-memory `moto` mock build
from it (`create_table_if_missing()` and `conftest.py`'s
`dynamodb_table` fixture respectively) — change the schema in exactly
one place, never hand-edit a `create_table` call somewhere else.

Every entity's key shape has a builder function in `db.py`
(`account_pk`, `finding_sk`, `gsi1_session_pk`, etc.) — routers should
never construct a `PK`/`SK`/`gsi*pk` string inline. This is what makes
it possible to know, just by reading `db.py`, exactly what every key in
the table looks like.

## Patterns worth reusing

- **PATCH endpoints** go through `dynamo_utils.build_update()`, which
  routes every field through `ExpressionAttributeNames` so a field
  named e.g. `status` — a DynamoDB reserved word — never collides with
  the expression syntax. Don't hand-build `UpdateExpression` strings.
- **"Not found" on PATCH** is a `ConditionExpression="attribute_exists(PK)"`
  caught as `ConditionalCheckFailedException`, not a separate
  `get_item` existence check first — one round trip, no TOCTOU gap.
- **Auth dependencies compose**: `get_authenticated_collector` (used by
  `heartbeat`/`tasks`/`observations`) and `get_current_account_id`
  (used by the web-app CRUD routes) are both plain FastAPI
  `Depends()` functions. Add a new route by depending on whichever one
  matches its caller — don't write a third auth mechanism.
- **Execution vs. business logic split**: replay protection
  (`consume_enrollment_token`'s conditional update), tier enforcement
  (`has_capacity`), and credential hashing all live in `auth.py`, not
  inline in `routers/collectors.py` — keeps the routers thin and the
  security-sensitive logic in one place to review.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Every test runs against an in-memory DynamoDB via `moto` — no AWS
credentials, no real table, no network calls. If a test needs real AWS
to pass, something's wrong; the whole point of `table_schema()` being
the single source of truth is that this shouldn't be necessary.

Cross-account isolation is tested explicitly, not assumed —
`test_cannot_patch_another_accounts_finding` proves one account's
partition key genuinely can't reach another's data. Add a test like
this for any new cross-entity access pattern, not just a "happy path"
test.

## Windows dev notes

Same as victor-collector: if PowerShell's execution policy blocks
`Activate.ps1`, call `.venv\Scripts\python.exe` / the venv's other
executables directly instead of activating.

## What's not built yet

- `api.victorrecon.com` as a custom domain — live today at its raw API
  Gateway URL, cert requested, pending one DNS record. See
  `DEPLOYMENT_STATUS.md` §4 in victor-recon-console.
- DynamoDB TTL on enrollment tokens — used/expired ones persist forever
  instead of being auto-cleaned. Not a security issue (already unusable
  once consumed or expired), just untidy.
- Deep AI triage on uploaded observations (`BACKLOG.md` M2 in
  victor-recon-console) — `/observations` currently just stores what's
  uploaded, deliberately not classifying it. See design doc §5 for why
  that's not a gap, it's the actual open-core boundary.

## License

Closed source — see `KILLER_FEATURES.md` §4.9 in victor-recon-console
for why this repo isn't the open-source one (that's `victor-collector`).
