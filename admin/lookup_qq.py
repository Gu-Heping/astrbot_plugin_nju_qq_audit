"""Read-only QQ history lookup for admin diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from config import redact_tokens_in_string
from data_source.students import PendingRequest
from storage.requests_store import utc_now_iso

LOOKUP_QQ_DEFAULT_LIMIT = 20
QQ_MIN_LEN = 5
QQ_MAX_LEN = 12

PARSED_QQ_KEYS = ("qq", "applicant_qq", "user_qq", "sender_id", "requester_id", "applicant_id")
AUDIT_QQ_KEYS = ("user_id", "qq", "requester_id", "applicant_id", "sender_id")
REJECT_AUDIT_SUFFIX = "_rejected"
AUDIT_TIME_KEYS = ("time", "timestamp", "created_at", "event_time")
AUDIT_PARSED_KEYS = ("name", "student_id", "major", "major_text", "profile", "_profile")
AUDIT_PARSED_BLOCKED_KEYS = frozenset(
    {"flag", "raw_event", "token", "cookie", "sanitized_raw", "event", "raw"}
)
_DATETIME_MIN_UTC = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class LookupQqRecord:
    request_id: str
    qq: str
    group_id: str
    created_at: str
    profile: str
    parsed: dict[str, Any]
    status: str
    decision: str
    reason: str
    match_strength: str
    source: str
    last_event_at: str = ""
    audit_types: list[str] = field(default_factory=list)
    admin_command: str | None = None
    blacklist_hit: bool = False
    exclusive_hit_group_ids: list[str] = field(default_factory=list)


@dataclass
class LookupQqResult:
    qq: str
    total: int
    records: list[LookupQqRecord]
    truncated: bool = False


def validate_lookup_qq(value: str) -> tuple[bool, str]:
    text = (value or "").strip()
    if not text:
        return False, "QQ 号不能为空"
    if not text.isdigit():
        return False, "QQ 号必须为纯数字"
    if len(text) < QQ_MIN_LEN or len(text) > QQ_MAX_LEN:
        return False, "QQ 号长度无效"
    return True, text


def _add_qq_value(values: set[str], raw: Any) -> None:
    if raw is None:
        return
    text = str(raw).strip()
    if text:
        values.add(text)


def _qq_values_from_mapping(data: dict[str, Any] | None, keys: tuple[str, ...]) -> set[str]:
    values: set[str] = set()
    if not isinstance(data, dict):
        return values
    for key in keys:
        _add_qq_value(values, data.get(key))
    event = data.get("event")
    if isinstance(event, dict):
        for key in keys:
            _add_qq_value(values, event.get(key))
    return values


def request_qq_values(req: PendingRequest) -> set[str]:
    values = _qq_values_from_mapping(
        {
            "user_id": req.user_id,
            **(req.parsed or {}),
        },
        ("user_id",) + PARSED_QQ_KEYS,
    )
    return values


def audit_record_qq_values(record: dict[str, Any]) -> set[str]:
    return _qq_values_from_mapping(record, AUDIT_QQ_KEYS)


def request_matches_qq(req: PendingRequest, qq: str) -> bool:
    return qq in request_qq_values(req)


def audit_record_matches_qq(record: dict[str, Any], qq: str) -> bool:
    return qq in audit_record_qq_values(record)


def lookup_display_status(status: str, decision: str = "") -> str:
    if status == "pending":
        return "pending"
    if status == "external":
        return "external"
    if status == "stale":
        return "stale"
    if status == "processed":
        if decision == "approve":
            return "approved"
        if decision == "reject":
            return "rejected"
        return "pending"
    if status == "dismissed":
        return "rejected"
    if status == "failed":
        return "pending"
    return status or "pending"


def infer_lookup_source(
    audit_types: list[str],
    *,
    admin_command: str | None = None,
) -> str:
    for audit_type in audit_types:
        text = (audit_type or "").lower()
        if text == "pending_reparsed" or "reparse" in text:
            return "reparse"
        if "catchup" in text:
            return "catchup"
        if "release" in text or text == "batch_release":
            return "release"
    cmd = (admin_command or "").lower()
    if "catchup" in cmd:
        return "catchup"
    if "release" in cmd or cmd == "approve":
        return "release"
    return "event"


def _audit_request_id(record: dict[str, Any]) -> str | None:
    req_id = record.get("request_id") or record.get("affected_request_id")
    if req_id:
        return str(req_id)
    return None


def _audit_group_id(record: dict[str, Any]) -> str:
    for key in ("group_id", "target_group_id"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    event = record.get("event")
    if isinstance(event, dict):
        for key in ("group_id", "target_group_id"):
            value = event.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _audit_user_id(record: dict[str, Any]) -> str:
    for key in AUDIT_QQ_KEYS:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    event = record.get("event")
    if isinstance(event, dict):
        for key in AUDIT_QQ_KEYS:
            value = event.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _audit_index(audit_log) -> dict[str, list[dict[str, Any]]]:
    by_request: dict[str, list[dict[str, Any]]] = {}
    for record in audit_log.read_all():
        if not isinstance(record, dict):
            continue
        req_id = _audit_request_id(record)
        if not req_id:
            continue
        by_request.setdefault(req_id, []).append(record)
    return by_request


def _record_source_for_status(status: str) -> str:
    if status == "pending":
        return "pending"
    return "history"


def _audit_types(events: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for record in events:
        audit_type = str(record.get("type") or "")
        if audit_type and audit_type not in seen:
            seen.add(audit_type)
            result.append(audit_type)
    return result


def _infer_audit_display_hints(events: list[dict[str, Any]], audit_types: list[str]) -> tuple[bool, list[str]]:
    blacklist_hit = any(
        audit_type == "blacklist_rejected" or "blacklist" in audit_type for audit_type in audit_types
    )
    exclusive_ids: list[str] = []
    for record in events:
        audit_type = str(record.get("type") or "")
        if "undergrad_exclusive" not in audit_type and "exclusive" not in audit_type:
            continue
        hit_ids = record.get("hit_group_ids")
        if isinstance(hit_ids, list) and hit_ids:
            exclusive_ids = [str(item) for item in hit_ids if item]
    return blacklist_hit, exclusive_ids


def _parse_audit_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _audit_record_datetime(record: dict[str, Any]) -> datetime | None:
    for key in AUDIT_TIME_KEYS:
        parsed = _parse_audit_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_audit_time(record: dict[str, Any]) -> str | None:
    for key in AUDIT_TIME_KEYS:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if _parse_audit_datetime(text) is None:
            continue
        return text
    return None


def _event_time(record: dict[str, Any]) -> str:
    return _extract_audit_time(record) or ""


def _parsed_from_audit_record(record: dict[str, Any]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    raw_parsed = record.get("parsed")
    if isinstance(raw_parsed, dict):
        for key, value in raw_parsed.items():
            if key in AUDIT_PARSED_BLOCKED_KEYS or key.startswith("_internal"):
                continue
            if key in AUDIT_PARSED_KEYS and value is not None and str(value).strip():
                parsed[key] = value
    for key in ("name", "student_id", "major", "major_text"):
        if key not in parsed:
            value = record.get(key)
            if value is not None and str(value).strip():
                parsed[key] = value
    if "_profile" not in parsed and "profile" not in parsed:
        profile = record.get("profile")
        if profile is not None and str(profile).strip():
            parsed["_profile"] = str(profile)
    elif "profile" in parsed and "_profile" not in parsed:
        parsed["_profile"] = parsed["profile"]
    return parsed


def _merge_parsed_from_audit_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in _sort_events(events):
        for key, value in _parsed_from_audit_record(record).items():
            if key not in merged and value is not None and str(value).strip():
                merged[key] = value
    return merged


def _is_terminal_audit_record(record: dict[str, Any]) -> bool:
    audit_type = str(record.get("type") or "")
    if audit_type.endswith(REJECT_AUDIT_SUFFIX) or audit_type == "blacklist_rejected":
        return True
    if audit_type == "action_called":
        return record.get("action") == "approve" and bool(record.get("ok"))
    if audit_type in {
        "action_already_approved",
        "action_already_refused",
        "request_stale",
        "request_stale_member_present",
        "external_handled",
        "external_approved",
        "external_handled_unknown",
        "external_rejected_inferred",
    }:
        return True
    if audit_type == "admin_command":
        return str(record.get("command") or record.get("action") or "") in {
            "approve",
            "reject",
            "dismiss",
        }
    return False


def _latest_audit_time(events: list[dict[str, Any]], *, terminal_only: bool) -> str | None:
    latest_dt: datetime | None = None
    latest_text: str | None = None
    for record in _sort_events(events):
        if terminal_only and not _is_terminal_audit_record(record):
            continue
        record_dt = _audit_record_datetime(record)
        audit_time = _extract_audit_time(record)
        if record_dt is None or not audit_time:
            continue
        if latest_dt is None or record_dt > latest_dt:
            latest_dt = record_dt
            latest_text = audit_time
    return latest_text


def _resolve_last_event_at(req: PendingRequest, events: list[dict[str, Any]]) -> str:
    created_at = str(req.created_at or "")
    if not events:
        return created_at or utc_now_iso()
    terminal_time = _latest_audit_time(events, terminal_only=True)
    if terminal_time:
        return terminal_time
    audit_time = _latest_audit_time(events, terminal_only=False)
    if audit_time:
        return audit_time
    return created_at or utc_now_iso()


def _sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda record: _audit_record_datetime(record) or _DATETIME_MIN_UTC)


def _latest_reason(events: list[dict[str, Any]]) -> str:
    for record in reversed(_sort_events(events)):
        reason = record.get("reason") or record.get("message") or record.get("dismiss_reason")
        if reason:
            return str(reason)
    return ""


def _resolve_lookup_reason(req: PendingRequest, events: list[dict[str, Any]]) -> str:
    audit_reason = _latest_reason(events)
    if audit_reason:
        return audit_reason
    return str(req.reason or "")


def _apply_terminal_from_audit(
    req: PendingRequest,
    events: list[dict[str, Any]],
) -> PendingRequest:
    status = req.status
    decision = req.decision
    reason = req.reason
    admin_command = req.admin_command
    processed_at = req.processed_at

    for record in _sort_events(events):
        audit_type = str(record.get("type") or "")
        if audit_type.endswith(REJECT_AUDIT_SUFFIX) or audit_type == "blacklist_rejected":
            status = "processed"
            decision = "reject"
            if record.get("reason"):
                reason = str(record.get("reason"))
            processed_at = processed_at or _event_time(record)
            continue

        if audit_type == "action_called":
            if record.get("action") == "approve" and record.get("ok"):
                status = "processed"
                decision = "approve"
                processed_at = processed_at or _event_time(record)
            continue

        if audit_type == "action_already_approved":
            terminal_status = str(record.get("status") or "processed")
            status = terminal_status if terminal_status in {"processed", "external"} else "processed"
            if status == "processed":
                decision = "approve"
            if record.get("reason"):
                reason = str(record.get("reason"))
            admin_command = str(record.get("admin_command") or admin_command or "")
            processed_at = processed_at or _event_time(record)
            continue

        if audit_type == "action_already_refused":
            status = "dismissed"
            decision = "reject"
            if record.get("reason"):
                reason = str(record.get("reason"))
            processed_at = processed_at or _event_time(record)
            continue

        if audit_type in {"request_stale", "request_stale_member_present"}:
            status = "external" if audit_type == "request_stale_member_present" else "stale"
            if record.get("reason"):
                reason = str(record.get("reason"))
            processed_at = processed_at or _event_time(record)
            continue

        if audit_type in {
            "external_handled",
            "external_approved",
            "external_handled_unknown",
            "external_rejected_inferred",
        }:
            status = "external"
            terminal_reason = record.get("message") or record.get("reason")
            if terminal_reason:
                reason = str(terminal_reason)
            processed_at = processed_at or _event_time(record)
            continue

        if audit_type == "admin_command":
            command = str(record.get("command") or record.get("action") or "")
            admin_command = command or admin_command
            if command == "approve":
                status = "processed"
                decision = "approve"
                processed_at = processed_at or _event_time(record)
            elif command == "reject":
                status = "processed"
                decision = "reject"
                processed_at = processed_at or _event_time(record)
            elif command == "dismiss":
                status = "dismissed"
                decision = "reject"
                if record.get("reason"):
                    reason = str(record.get("reason"))
                processed_at = processed_at or _event_time(record)

    return replace(
        req,
        status=status,
        decision=decision,
        reason=reason,
        admin_command=admin_command,
        processed_at=processed_at,
    )


def _base_request_from_audit(req_id: str, events: list[dict[str, Any]]) -> PendingRequest | None:
    base: dict[str, Any] | None = None
    created_at = ""
    for record in _sort_events(events):
        audit_type = str(record.get("type") or "")
        if audit_type in {"decision_made", "request_received"} or record.get("decision"):
            base = record
            created_at = _event_time(record) or created_at
            break
    if base is None:
        for record in _sort_events(events):
            group_id = _audit_group_id(record)
            user_id = _audit_user_id(record)
            if group_id and user_id:
                base = record
                created_at = _event_time(record) or created_at
                break
    if base is None:
        return None

    profile = str(base.get("profile") or "undergraduate")
    match_strength = str(base.get("match_strength") or base.get("new_match_strength") or "none")
    parsed = _merge_parsed_from_audit_events(events)
    if "_profile" in parsed:
        profile = str(parsed.get("_profile") or parsed.get("profile") or profile)
    return PendingRequest(
        id=req_id,
        group_id=_audit_group_id(base),
        user_id=_audit_user_id(base),
        comment=str(base.get("comment") or ""),
        flag="",
        sub_type="add",
        parsed=parsed,
        match={"strength": match_strength},
        decision=str(base.get("decision") or base.get("new_decision") or "manual_review"),
        confidence=float(base.get("confidence") or 0),
        reason=str(base.get("reason") or base.get("new_reason") or base.get("message") or ""),
        mode=str(base.get("mode") or "record-only"),
        status=str(base.get("status") or "pending"),
        created_at=created_at or utc_now_iso(),
        match_strength=match_strength,
        profile=profile,
    )


def _to_lookup_record(
    req: PendingRequest,
    events: list[dict[str, Any]],
    *,
    source: str,
    qq: str,
) -> LookupQqRecord:
    types = _audit_types(events)
    blacklist_hit, exclusive_ids = _infer_audit_display_hints(events, types)

    if events:
        terminal = _apply_terminal_from_audit(req, events)
        status = str(terminal.status or "pending")
        decision = str(terminal.decision or "")
        admin_command = terminal.admin_command
        reason = _resolve_lookup_reason(req, events)
    else:
        status = str(req.status or "pending")
        decision = str(req.decision or "")
        admin_command = req.admin_command
        reason = str(req.reason or "")

    parsed = dict(req.parsed or {})
    if blacklist_hit:
        parsed.setdefault("_blacklist_hit", True)
    if exclusive_ids and not parsed.get("_undergrad_exclusive_group_ids"):
        parsed["_undergrad_exclusive_hit"] = True
        parsed["_undergrad_exclusive_group_ids"] = exclusive_ids
    created_at = str(req.created_at or "")
    last_event_at = _resolve_last_event_at(req, events)
    return LookupQqRecord(
        request_id=req.id,
        qq=qq,
        group_id=str(req.group_id or ""),
        created_at=created_at,
        profile=str(getattr(req, "profile", None) or parsed.get("_profile") or "undergraduate"),
        parsed=parsed,
        status=status,
        decision=decision,
        reason=reason,
        match_strength=str(req.match_strength or (req.match or {}).get("strength") or "none"),
        source=source,
        last_event_at=last_event_at,
        audit_types=types,
        admin_command=admin_command,
        blacklist_hit=blacklist_hit or bool(parsed.get("_blacklist_hit")),
        exclusive_hit_group_ids=exclusive_ids or list(parsed.get("_undergrad_exclusive_group_ids") or []),
    )


def _collect_from_requests_store(
    qq: str,
    all_requests: list[PendingRequest],
    audit_by_request: dict[str, list[dict[str, Any]]],
) -> dict[str, LookupQqRecord]:
    records: dict[str, LookupQqRecord] = {}
    for req in all_requests:
        if not request_matches_qq(req, qq):
            continue
        events = audit_by_request.get(req.id, [])
        source = _record_source_for_status(req.status or "pending")
        records[req.id] = _to_lookup_record(req, events, source=source, qq=qq)
    return records


def _collect_from_audit_log(
    qq: str,
    audit_by_request: dict[str, list[dict[str, Any]]],
    known_ids: set[str],
) -> dict[str, LookupQqRecord]:
    records: dict[str, LookupQqRecord] = {}
    for req_id, events in audit_by_request.items():
        if req_id in known_ids:
            continue
        if not any(audit_record_matches_qq(event, qq) for event in events):
            continue
        base = _base_request_from_audit(req_id, events)
        if base is None:
            continue
        records[req_id] = _to_lookup_record(
            base,
            events,
            source="audit",
            qq=qq,
        )
    return records


def _record_sort_datetime(value: str) -> datetime:
    return _parse_audit_datetime(value) or _DATETIME_MIN_UTC


def _sort_key(record: LookupQqRecord) -> datetime:
    return _record_sort_datetime(record.last_event_at or record.created_at or "")


async def lookup_qq_records(
    requests_store,
    audit_log,
    qq: str,
    *,
    limit: int = LOOKUP_QQ_DEFAULT_LIMIT,
) -> LookupQqResult:
    ok, value = validate_lookup_qq(qq)
    if not ok:
        raise ValueError(value)

    audit_by_request = _audit_index(audit_log)
    all_requests = await requests_store.list_all()

    merged = _collect_from_requests_store(value, all_requests, audit_by_request)
    merged.update(
        _collect_from_audit_log(value, audit_by_request, set(merged.keys()))
    )

    ordered = sorted(merged.values(), key=_sort_key, reverse=True)
    total = len(ordered)
    page = ordered[:limit]

    return LookupQqResult(
        qq=value,
        total=total,
        records=page,
        truncated=total > limit,
    )


def sanitize_lookup_output(text: str, settings=None) -> str:
    if settings is not None:
        return redact_tokens_in_string(text, settings)
    return text
