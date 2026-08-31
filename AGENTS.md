# Working in this repo

The design source of truth is
[`docs/cloud-api-design.md`](https://github.com/babbabooieq/victor-recon-console/blob/main/docs/cloud-api-design.md)
in **victor-recon-console**, not here — DynamoDB schema, auth model, full
API surface, deployment plan. If this repo and that doc disagree, that's
a bug in one of them (usually the doc, since it was written before this
existed — keep it updated when you change something here, several
things already got fixed this way: the missing `RawObservation` and
enrollment-token table rows, the auth placeholder note).

## The one thing that actually matters: `deps.py`

`get_current_account_id()` trusts a caller-supplied `X-Account-Id`
header with **zero verification**. This is a deliberate placeholder,
not an oversight — real auth (Cognito, proposed but not confirmed, see
the design doc §3/§8) will replace it, and every CRUD route depends on
this function returning a verified account_id, not on how it's
verified. **Do not deploy this behind a public endpoint as-is** — any
caller can currently claim to be any account. If you're touching auth,
read the design doc's §3 and §8 first.

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

- Cognito (or whatever human auth ends up being) — see the `deps.py`
  warning above.
- Actual AWS deployment — this has been built and tested locally only.
  `docs/cloud-api-design.md` §6 has the deployment plan (DynamoDB table,
  Lambda, API Gateway, `api.victorrecon.com`) once it's time.
- Deep AI triage on uploaded observations (`BACKLOG.md` M2 in
  victor-recon-console) — `/observations` currently just stores what's
  uploaded, deliberately not classifying it. See design doc §5 for why
  that's not a gap, it's the actual open-core boundary.

## License

Closed source — see `KILLER_FEATURES.md` §4.9 in victor-recon-console
for why this repo isn't the open-source one (that's `victor-collector`).
