# victor-cloud-api

VICTOR Recon's cloud API — accounts, sessions, findings, and the server
side of the collector's wire protocol. **Closed source**, unlike
[victor-collector](https://github.com/musashiandy/victor-collector) — see
[`KILLER_FEATURES.md` §4.9](https://github.com/babbabooieq/victor-recon-console/blob/main/KILLER_FEATURES.md)
in victor-recon-console for why the open-core split lands here.

> **Status: early v0.1.** `POST /v1/collectors/enroll` is real:
> one-time-token exchange, per-tier collector-count enforcement, and a
> credential that resolves back to its collector via one DynamoDB query.
> Everything else in [`docs/cloud-api-design.md`](https://github.com/babbabooieq/victor-recon-console/blob/main/docs/cloud-api-design.md)'s
> §7 MVP checklist (heartbeat, tasks, observations, the web app's CRUD
> contract) isn't built yet.

## What this is

The piece both [victor-recon-console](https://github.com/babbabooieq/victor-recon-console)
(the web app) and [victor-collector](https://github.com/musashiandy/victor-collector)
already assume exists — see `docs/system-architecture.md` in the console
repo for the three-layer picture. Full design (DynamoDB schema, auth,
API surface, deployment) is in `docs/cloud-api-design.md`; this README
only covers what's actually built.

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
