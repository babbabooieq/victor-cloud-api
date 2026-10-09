# victor-cloud-api

> **October 2026 local update:** task claims/leases, terminal outcomes, immutable
> attributed uploads, collector/task/observation reads and expiring exact-target
> authorization are implemented locally. These changes have not been deployed.
> The historical production status below describes v0.1. The coordinated release
> contract and route authorization requirements are in the console repo's
> `docs/live-workspace-protocol.md`.

VICTOR Recon's cloud API — accounts, sessions, findings, and the server
side of the collector's wire protocol. **Closed source**, unlike
[victor-collector](https://github.com/musashiandy/victor-collector) — see
[`KILLER_FEATURES.md` §4.9](https://github.com/babbabooieq/victor-recon-console/blob/main/KILLER_FEATURES.md)
in victor-recon-console for why the open-core split lands here.

> **Status: v0.1 live in production.** The full collector wire protocol
> (enroll, heartbeat, tasks, observations) and the web app's CRUD
> contract (findings, sessions, targets) are real — 40/40 tests passing.
> Human auth is real Cognito, not a placeholder, with an API Gateway JWT
> authorizer in front of every web-app route. Deployed to AWS and proven
> end to end through the actual browser sign-up flow, not just tests —
> see `DEPLOYMENT_STATUS.md` in victor-recon-console for the full
> account, including two real bugs (missing CORS, missing account
> auto-provisioning) that only surfaced against a real browser.

## What this is

The piece both [victor-recon-console](https://github.com/babbabooieq/victor-recon-console)
(the web app) and [victor-collector](https://github.com/musashiandy/victor-collector)
already assume exists — see `docs/system-architecture.md` in the console
repo for the three-layer picture. Full design (DynamoDB schema, auth,
API surface, deployment) is in `docs/cloud-api-design.md`; this README
only covers what's actually built.

## API surface

Collector-facing (bearer credential auth — `resolve_collector_by_credential`):

```
POST /v1/collectors/enroll
POST /v1/collectors/heartbeat
GET  /v1/collectors/tasks
POST /v1/sessions/{session_id}/observations
```

Web app CRUD (real Cognito JWT auth in production; a local-dev-only
header fallback exists behind an explicit opt-in — see `AGENTS.md`):

```
GET/POST   /v1/findings              PATCH /v1/findings/{id}
GET/POST   /v1/sessions              PATCH /v1/sessions/{id}
GET/POST   /v1/targets
POST       /v1/accounts/me                          (idempotent — provisions the account)
POST       /v1/collectors/enrollment-tokens         ("Add a Collector" button)
```

## Install

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows (or call
                             # .venv\Scripts\python.exe directly if your
                             # PowerShell execution policy blocks .ps1)
pip install -e ".[dev]"
```

## Test

```bash
pytest
```

Runs entirely against an in-memory DynamoDB via [moto](https://github.com/getmoto/moto)
— no AWS credentials or real table needed. `db.py`'s `table_schema()` is
the single source of truth both the real table and moto's mock build
from, so they can't drift apart.

## Run locally

```bash
export VICTOR_TABLE_NAME=victor-recon-api-dev   # or set in your shell profile
uvicorn victor_cloud_api.app:app --reload
```

Needs real AWS credentials and a table matching `db.table_schema()` —
there's no local-only mode for the API itself (unlike the collector's
Standalone Mode, this service has no reason to run without AWS).

## License

Closed source. Not published under MIT — see `KILLER_FEATURES.md` §4.9
in victor-recon-console.
