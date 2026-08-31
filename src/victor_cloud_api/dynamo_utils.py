"""Shared DynamoDB update-expression builder for PATCH endpoints.

Every field goes through ExpressionAttributeNames (never inlined into the
expression string) so a field named e.g. "status" - a DynamoDB reserved
word - never collides. Same trick already used in collectors.py's
heartbeat handler; this generalizes it for arbitrary partial updates.
"""

from __future__ import annotations


def build_update(updates: dict) -> tuple[str, dict, dict]:
    set_parts = []
    names = {}
    values = {}
    for i, (key, value) in enumerate(updates.items()):
        set_parts.append(f"#f{i} = :v{i}")
        names[f"#f{i}"] = key
        values[f":v{i}"] = value
    return "SET " + ", ".join(set_parts), names, values
