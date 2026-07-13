from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MatchKind = Literal["unique", "ambiguous", "none"]
ParserVariant = Literal["snowluma_list", "napcat_dict", "parse_failed", "empty"]


@dataclass(frozen=True)
class SystemJoinRequest:
    group_id: str
    requester_uin: str
    flag: str | None = None
    request_id: str | None = None
    comment: str | None = None


@dataclass(frozen=True)
class PendingMatchResult:
    kind: MatchKind
    entry: SystemJoinRequest | None = None
    match_by: str | None = None


@dataclass(frozen=True)
class ParsedGroupSystemMsg:
    entries: list[SystemJoinRequest]
    variant: ParserVariant
    top_level_shape: str
    request_count: int
    first_request_fields: list[str]


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_slreq_flag(flag: str | None) -> tuple[str | None, str | None]:
    """Parse SnowLuma flag `slreq:{type}:{request_id}:{group_id}:...`."""
    if not flag or not flag.startswith("slreq:"):
        return None, None
    parts = flag.split(":")
    if len(parts) < 4:
        return None, None
    request_id = parts[2].strip() or None
    group_id = parts[3].strip() or None
    return request_id, group_id


def normalize_join_entry(raw: dict[str, Any]) -> SystemJoinRequest | None:
    group_id = _as_str(raw.get("group_id"))
    if not group_id:
        return None
    # requester_uin may be 0 on SnowLuma; do not treat 0 as missing via `or`.
    if "requester_uin" in raw and raw.get("requester_uin") is not None:
        requester = _as_str(raw.get("requester_uin"))
    else:
        requester = _as_str(raw.get("user_id"))
    if requester is None:
        requester = "0"
    request_id = _as_str(raw.get("request_id") or raw.get("seq") or raw.get("sequence"))
    flag = _as_str(raw.get("flag"))
    if not request_id and flag:
        parsed_rid, _ = parse_slreq_flag(flag)
        request_id = parsed_rid
    return SystemJoinRequest(
        group_id=group_id,
        requester_uin=requester,
        flag=flag,
        request_id=request_id,
        comment=_as_str(raw.get("message") or raw.get("comment")),
    )


def _entries_from_raw_list(raw_list: list[Any]) -> list[SystemJoinRequest]:
    entries: list[SystemJoinRequest] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        entry = normalize_join_entry(raw)
        if entry is not None:
            entries.append(entry)
    return entries


def parse_group_system_msg_data(data: Any) -> ParsedGroupSystemMsg:
    if isinstance(data, list):
        entries = _entries_from_raw_list(data)
        first_fields: list[str] = []
        if data and isinstance(data[0], dict):
            first_fields = sorted(str(k) for k in data[0].keys())
        return ParsedGroupSystemMsg(
            entries=entries,
            variant="snowluma_list",
            top_level_shape="list",
            request_count=len(entries),
            first_request_fields=first_fields,
        )

    if isinstance(data, dict):
        bucket = data.get("join_requests")
        if not isinstance(bucket, list):
            bucket = data.get("requests")
        if not isinstance(bucket, list):
            bucket = data.get("invited_requests")
        if not isinstance(bucket, list):
            bucket = []
        entries = _entries_from_raw_list(bucket)
        first_fields: list[str] = []
        if bucket and isinstance(bucket[0], dict):
            first_fields = sorted(str(k) for k in bucket[0].keys())
        return ParsedGroupSystemMsg(
            entries=entries,
            variant="napcat_dict",
            top_level_shape="dict",
            request_count=len(entries),
            first_request_fields=first_fields,
        )

    return ParsedGroupSystemMsg(
        entries=[],
        variant="parse_failed",
        top_level_shape=type(data).__name__,
        request_count=0,
        first_request_fields=[],
    )


def describe_group_system_msg_result(result: Any) -> dict[str, Any]:
    """Sanitized probe fields for /audit debug (no tokens/raw events)."""
    ok = bool(getattr(result, "ok", False))
    retcode = getattr(result, "retcode", None)
    data = getattr(result, "data", None)
    parsed = parse_group_system_msg_data(data) if ok else None
    return {
        "action_status": "ok" if ok else "failed",
        "retcode": retcode,
        "data_type": type(data).__name__,
        "request_count": parsed.request_count if parsed else 0,
        "top_level_shape": parsed.top_level_shape if parsed else type(data).__name__,
        "first_request_fields": (
            ",".join(parsed.first_request_fields) if parsed and parsed.first_request_fields else ""
        ),
        "parser_variant": parsed.variant if parsed else "unavailable",
    }


def filter_entries_for_group(
    entries: list[SystemJoinRequest], group_id: str
) -> list[SystemJoinRequest]:
    return [entry for entry in entries if entry.group_id == group_id]


def _requester_uin_usable(requester_uin: str | None) -> bool:
    if requester_uin is None:
        return False
    text = str(requester_uin).strip()
    if not text:
        return False
    try:
        return int(text) > 0
    except ValueError:
        return True


def match_pending_to_entries(
    *,
    flag: str,
    group_id: str,
    user_id: str,
    comment: str,
    entries: list[SystemJoinRequest],
) -> PendingMatchResult:
    if not entries:
        return PendingMatchResult(kind="none")

    # 1) Full SnowLuma / platform flag
    if flag:
        by_flag = [entry for entry in entries if entry.flag and entry.flag == flag]
        if len(by_flag) == 1:
            return PendingMatchResult(kind="unique", entry=by_flag[0], match_by="flag")
        if len(by_flag) > 1:
            return PendingMatchResult(kind="ambiguous", match_by="flag")

    # 2) request_id/sequence parsed from slreq flag + group_id
    flag_request_id, flag_group_id = parse_slreq_flag(flag)
    target_group = flag_group_id or group_id
    if flag_request_id:
        by_rid = [
            entry
            for entry in entries
            if entry.request_id == flag_request_id and entry.group_id == target_group
        ]
        if len(by_rid) == 1:
            return PendingMatchResult(
                kind="unique", entry=by_rid[0], match_by="slreq_request_id"
            )
        if len(by_rid) > 1:
            return PendingMatchResult(kind="ambiguous", match_by="slreq_request_id")

    # 3) group_id + requester_uin only when requester_uin > 0
    if _requester_uin_usable(user_id):
        by_user = [
            entry
            for entry in entries
            if entry.group_id == group_id
            and entry.requester_uin == user_id
            and _requester_uin_usable(entry.requester_uin)
        ]
        if len(by_user) == 1:
            return PendingMatchResult(
                kind="unique", entry=by_user[0], match_by="group_user"
            )
        if len(by_user) > 1:
            return PendingMatchResult(kind="ambiguous", match_by="group_user")

    # 4) comment as auxiliary only
    normalized_comment = (comment or "").strip()
    if normalized_comment:
        by_comment = [
            entry
            for entry in entries
            if (entry.comment or "").strip() == normalized_comment
            and entry.group_id == group_id
        ]
        if len(by_comment) == 1:
            return PendingMatchResult(
                kind="unique", entry=by_comment[0], match_by="comment"
            )
        if len(by_comment) > 1:
            return PendingMatchResult(kind="ambiguous", match_by="comment")

    return PendingMatchResult(kind="none")


def snapshot_index(entries: list[SystemJoinRequest]) -> dict[str, Any]:
    flags: list[str] = []
    user_keys: list[str] = []
    request_ids: list[str] = []
    for entry in entries:
        if entry.flag:
            flags.append(entry.flag)
        if _requester_uin_usable(entry.requester_uin):
            user_keys.append(f"{entry.group_id}:{entry.requester_uin}")
        if entry.request_id:
            request_ids.append(f"{entry.group_id}:{entry.request_id}")
    return {
        "flags": sorted(set(flags)),
        "user_keys": sorted(set(user_keys)),
        "request_ids": sorted(set(request_ids)),
    }


def pending_seen_in_snapshot(
    *,
    flag: str,
    group_id: str,
    user_id: str,
    snapshot: dict[str, Any] | None,
) -> bool:
    if not snapshot:
        return False
    flags = set(snapshot.get("flags") or [])
    if flag and flag in flags:
        return True
    flag_request_id, flag_group_id = parse_slreq_flag(flag)
    request_ids = set(snapshot.get("request_ids") or [])
    if flag_request_id:
        key = f"{flag_group_id or group_id}:{flag_request_id}"
        if key in request_ids:
            return True
    if _requester_uin_usable(user_id):
        user_keys = set(snapshot.get("user_keys") or [])
        if f"{group_id}:{user_id}" in user_keys:
            return True
    return False
