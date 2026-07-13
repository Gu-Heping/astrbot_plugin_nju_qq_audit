from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from probe.sanitizer import get_field


def comment_hash(comment: str) -> str:
    return hashlib.sha256((comment or "").encode("utf-8")).hexdigest()[:16]


def extract_event_time_iso(raw_event: dict[str, Any] | None) -> str | None:
    if not raw_event:
        return None
    raw_time = get_field(raw_event, "time")
    if raw_time is None:
        return None
    try:
        ts = int(raw_time)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def compute_event_fingerprint(
    *,
    group_id: str,
    user_id: str,
    flag: str,
    event_time: str | None,
    comment: str,
    sub_type: str,
) -> str:
    payload = "|".join(
        [
            group_id,
            user_id,
            flag,
            event_time or "",
            comment_hash(comment),
            sub_type,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
