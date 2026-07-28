from __future__ import annotations

import json
import logging

from admin.labels import DEFAULT_REJECT_REASON

logger = logging.getLogger(__name__)


def normalize_qq_reject_reason(reason: str | None) -> str:
    """Remove accidental quote/JSON wrapping from config or upstream strings."""
    text = (reason or "").strip()
    if not text:
        return ""

    while len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        text = text[1:-1].strip()

    if text.startswith('"') and text.endswith('"'):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str):
            text = decoded.strip()

    return text


def log_reject_reason_generated(reject_reason: str, *, source: str) -> None:
    logger.debug(
        "[reject debug] generated reject_reason source=%s value=%r",
        source,
        reject_reason,
    )


def log_reject_reason_before_send(
    reason: str,
    *,
    flag: str,
    sub_type: str,
    approve: bool,
) -> None:
    if approve:
        return
    logger.warning(
        "[reject debug] before set_group_add_request: reason=%r type=%s flag=%s sub_type=%s",
        reason,
        type(reason).__name__,
        flag,
        sub_type,
    )


def resolve_qq_reject_reason(reason: str | None, *, fallback: str | None = None) -> str:
    """Normalize and ensure a non-empty QQ-facing reject reason."""
    text = normalize_qq_reject_reason(reason)
    if text:
        return text
    backup = normalize_qq_reject_reason(fallback)
    if backup:
        return backup
    return DEFAULT_REJECT_REASON


def log_reject_dispatch(
    *,
    source: str,
    group_id: str,
    user_id: str,
    flag: str,
    reason: str,
    decision: str | None = None,
) -> None:
    logger.info(
        "[reject dispatch] source=%s group=%s user=%s flag=%s reason=%r",
        source,
        group_id,
        user_id,
        flag,
        reason,
    )
    if source == "manual":
        return
    logger.warning(
        "[auto reject debug] flag=%s group=%s decision=%s reason=%r type=%s",
        flag,
        group_id,
        decision or source,
        reason,
        type(reason).__name__,
    )
