"""Validation for one host/IP or HTTP URL; scan ranges and argument lists are excluded."""
import ipaddress
import re
from urllib.parse import urlsplit


def validate_target(value: str) -> str:
    if not value or value != value.strip() or any(char.isspace() for char in value):
        raise ValueError("Use a single exact host, IP or HTTP URL")
    host = value
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Only HTTP URLs without embedded credentials are supported")
        _ = parsed.port  # Reject malformed/out-of-range ports.
        host = parsed.hostname
    try:
        ipaddress.ip_address(host)
        return value
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    if len(host) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
        raise ValueError("Use a single hostname/IP; ranges, CIDRs and target lists are not supported")
    return value
