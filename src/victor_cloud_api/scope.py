"""v1 scope: immutable exact-target authorization snapshots, not a CIDR policy engine."""
from datetime import datetime, timezone

from fastapi import HTTPException

from victor_cloud_api.models import AuthorizationRecord


def validate_authorization(session: dict, plugin: str, target: str) -> AuthorizationRecord:
    raw = session.get("authorization")
    if not raw:
        raise HTTPException(status_code=422, detail="Create a session with an expiring authorization record first")
    record = AuthorizationRecord.model_validate(raw)
    if record.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="Authorization expired; create a newly authorized session")
    if target != record.target or target != session["target"]:
        raise HTTPException(status_code=422, detail="Target is outside this authorization record")
    if plugin not in record.allowed_plugins:
        raise HTTPException(status_code=422, detail="Plugin is outside this authorization record")
    return record
