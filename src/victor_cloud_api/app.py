"""Lambda entry point. One function, internal routing via FastAPI - see
docs/cloud-api-design.md §1 in victor-recon-console for why (matches the
frontend's single-Lambda SSR pattern rather than a function per route).
"""

from __future__ import annotations

from fastapi import FastAPI
from mangum import Mangum

from victor_cloud_api.routers import collectors, findings, observations, sessions, targets

app = FastAPI(title="VICTOR Recon Cloud API")
app.include_router(collectors.router)
app.include_router(observations.router)
app.include_router(findings.router)
app.include_router(sessions.router)
app.include_router(targets.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


handler = Mangum(app)
