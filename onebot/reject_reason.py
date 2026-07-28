from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from admin.labels import DEFAULT_REJECT_REASON

logger = logging.getLogger(__name__)

_reject_wire_call_counts: dict[str, int] = {}


def _parse_request_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reject_elapsed_sec(request_time: str | None, reject_at: datetime) -> str:
    parsed = _parse_request_time(request_time)
    if parsed is None:
        return "-"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    elapsed = (reject_at - parsed).total_seconds()
    return f"{elapsed:.3f}"


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
        return str(text)
    backup = normalize_qq_reject_reason(fallback)
    if backup:
        return str(backup)
    return DEFAULT_REJECT_REASON


def log_reject_final_payload(
    *,
    source: str,
    flag: str,
    sub_type: str,
    approve: bool,
    reason: str,
    request_id: str | None = None,
) -> None:
    at = datetime.now(timezone.utc).isoformat()
    logger.warning(
        "[reject final payload] at=%s source=%s request_id=%s flag=%r sub_type=%r "
        "approve=%r reason=%r",
        at,
        source,
        request_id or "-",
        flag,
        sub_type,
        approve,
        reason,
    )


def log_snowluma_reject_wire(
    *,
    backend: str,
    flag: str,
    sub_type: str,
    approve: bool,
    reason: str,
    request_id: str | None = None,
    reject_source: str | None = None,
    request_time: str | None = None,
) -> None:
    """Log the exact dict kwargs sent to SnowLuma/OneBot for reject calls."""
    count = _reject_wire_call_counts.get(flag, 0) + 1
    _reject_wire_call_counts[flag] = count
    reject_at = datetime.now(timezone.utc)
    at = reject_at.isoformat()
    elapsed = _reject_elapsed_sec(request_time, reject_at)
    reason_len = len(reason)
    logger.warning(
        "[snowluma reject wire] at=%s backend=%s reject_source=%s request_id=%s "
        "request_time=%s reject_elapsed_sec=%s reason_len=%s "
        "flag=%r sub_type=%r approve=%r reason=%r call_index=%s",
        at,
        backend,
        reject_source or "-",
        request_id or "-",
        request_time or "-",
        elapsed,
        reason_len,
        flag,
        sub_type,
        approve,
        reason,
        count,
    )
    if count > 1:
        logger.warning(
            "[snowluma reject duplicate] flag=%r call_index=%s request_id=%s "
            "reject_source=%s request_time=%s reject_elapsed_sec=%s reason_len=%s at=%s",
            flag,
            count,
            request_id or "-",
            reject_source or "-",
            request_time or "-",
            elapsed,
            reason_len,
            at,
        )


def log_snowluma_reject_result(
    *,
    flag: str,
    reject_source: str | None,
    request_id: str | None,
    retcode: int | None,
    status: str,
    message: str | None,
    call_index: int | None = None,
    request_time: str | None = None,
    reason_len: int | None = None,
) -> None:
    reject_at = datetime.now(timezone.utc)
    at = reject_at.isoformat()
    index = call_index if call_index is not None else _reject_wire_call_counts.get(flag, 0)
    elapsed = _reject_elapsed_sec(request_time, reject_at)
    logger.warning(
        "[snowluma reject result] at=%s flag=%r reject_source=%s request_id=%s "
        "request_time=%s reject_elapsed_sec=%s reason_len=%s "
        "call_index=%s retcode=%s status=%s message=%r",
        at,
        flag,
        reject_source or "-",
        request_id or "-",
        request_time or "-",
        elapsed,
        reason_len if reason_len is not None else "-",
        index,
        retcode,
        status,
        message,
    )


def reset_reject_wire_call_counts_for_tests() -> None:
    _reject_wire_call_counts.clear()


def log_reject_delay(
    *,
    source: str,
    flag: str,
    group_id: str,
    delay: float,
) -> None:
    logger.info(
        "[reject delay] source=%s flag=%s group_id=%s delay=%s",
        source,
        flag,
        group_id,
        delay,
    )


def log_reject_dispatch(
    *,
    source: str,
    group_id: str,
    user_id: str,
    flag: str,
    reason: str,
    delay: float,
    sub_type: str | None = None,
    approve: bool = False,
    decision: str | None = None,
) -> None:
    logger.info(
        "[reject dispatch] source=%s group=%s user=%s flag=%s reason=%r delay=%s",
        source,
        group_id,
        user_id,
        flag,
        reason,
        delay,
    )
    if sub_type is not None:
        logger.debug(
            "[reject dispatch] sub_type=%s approve=%s",
            sub_type,
            approve,
        )
    if source == "manual":
        return
    logger.warning(
        "[auto reject debug] flag=%s group=%s decision=%s reason=%r type=%s delay=%s",
        flag,
        group_id,
        decision or source,
        reason,
        type(reason).__name__,
        delay,
    )
