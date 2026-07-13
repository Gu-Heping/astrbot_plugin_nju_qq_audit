from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from data_source.students import PendingRequest
from onebot.group_system_msg import (
    SystemJoinRequest,
    match_pending_to_entries,
    parse_group_system_msg_data,
    pending_seen_in_snapshot,
    snapshot_index,
)


@dataclass
class PendingReconcileSummary:
    external_approved: int = 0
    external_rejected_inferred: int = 0
    external_handled_unknown: int = 0
    unchanged: int = 0
    skipped_ambiguous: int = 0
    failed: bool = False
    failure_message: str | None = None
    snowluma_empty_ambiguity: bool = False

    def to_display_lines(self) -> list[str]:
        if self.failed:
            return ["QQ 状态同步失败，本次展示本地队列"]
        lines = [
            "本次自动同步：",
            f"外部同意：{self.external_approved}",
            f"外部拒绝（推断）：{self.external_rejected_inferred}",
            f"状态不明：{self.external_handled_unknown}",
        ]
        if self.skipped_ambiguous:
            lines.append(f"匹配不唯一：{self.skipped_ambiguous}")
        if self.snowluma_empty_ambiguity:
            lines.append(
                "说明：SnowLuma 空列表无法区分查询失败与真实无申请，拒绝仅在多次确认后推断。"
            )
        return lines


@dataclass
class GroupSnapshotFetch:
    ok: bool
    reliable: bool
    entries: list[SystemJoinRequest] = field(default_factory=list)
    message: str | None = None
    index: dict[str, Any] = field(default_factory=dict)
    parser_variant: str | None = None
    top_level_shape: str | None = None
    empty_untrusted: bool = False


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_disappearance(
    *,
    pending: PendingRequest,
    current_entries: list[SystemJoinRequest],
    previous_index: dict[str, Any] | None,
    member_present: bool | None,
    absence_state: dict[str, Any] | None,
    reject_confirm_snapshots: int = 2,
    reject_wait_seconds: int = 30,
    now: datetime | None = None,
) -> str:
    """Return reconcile action id or 'unchanged'/'ambiguous'/'external_handled_unknown'.

    SnowLuma may return a successful empty list on internal failure, so external
    reject requires multiple spaced successful absences + member_not_found.
    """
    match = match_pending_to_entries(
        flag=pending.flag,
        group_id=pending.group_id,
        user_id=pending.user_id,
        comment=pending.comment or "",
        entries=current_entries,
    )
    if match.kind == "ambiguous":
        return "ambiguous"
    if match.kind == "unique":
        return "unchanged"

    if not previous_index:
        return "unchanged"
    if not pending_seen_in_snapshot(
        flag=pending.flag,
        group_id=pending.group_id,
        user_id=pending.user_id,
        snapshot=previous_index,
    ):
        # Also accept evidence from absence_state.last_seen snapshot chain.
        if not (absence_state and absence_state.get("seen_before_absent")):
            return "unchanged"

    if member_present is True:
        return "external_approved"

    if member_present is False:
        consecutive = int((absence_state or {}).get("consecutive_absent") or 0)
        first_absent_at = _parse_iso((absence_state or {}).get("first_absent_at"))
        now_dt = now or datetime.now(timezone.utc)
        waited = (
            (now_dt - first_absent_at).total_seconds()
            if first_absent_at is not None
            else 0.0
        )
        if (
            consecutive >= max(2, reject_confirm_snapshots)
            and waited >= max(0, reject_wait_seconds)
        ):
            return "external_rejected_inferred"
        return "external_handled_unknown"

    return "external_handled_unknown"


def next_absence_state(
    *,
    currently_present: bool,
    previous: dict[str, Any] | None,
    seen_in_history: bool,
    now_iso: str,
) -> dict[str, Any] | None:
    if currently_present:
        return None
    if not seen_in_history and not (previous and previous.get("seen_before_absent")):
        return None
    if previous and previous.get("seen_before_absent"):
        return {
            "seen_before_absent": True,
            "first_absent_at": previous.get("first_absent_at") or now_iso,
            "consecutive_absent": int(previous.get("consecutive_absent") or 0) + 1,
            "last_absent_at": now_iso,
        }
    return {
        "seen_before_absent": True,
        "first_absent_at": now_iso,
        "consecutive_absent": 1,
        "last_absent_at": now_iso,
    }


def build_group_snapshot_fetch(result) -> GroupSnapshotFetch:
    """Build snapshot from ActionResult-like object with ok/message/data."""
    if not getattr(result, "ok", False):
        return GroupSnapshotFetch(
            ok=False,
            reliable=False,
            message=getattr(result, "message", None) or "query failed",
        )
    data = getattr(result, "data", None)
    parsed = parse_group_system_msg_data(data)
    if parsed.variant == "parse_failed":
        return GroupSnapshotFetch(
            ok=False,
            reliable=False,
            message="invalid group system msg payload",
            parser_variant=parsed.variant,
            top_level_shape=parsed.top_level_shape,
        )
    empty_untrusted = parsed.request_count == 0
    return GroupSnapshotFetch(
        ok=True,
        # Empty success is accepted but not trusted as definitive absence alone.
        reliable=True,
        entries=parsed.entries,
        message=getattr(result, "message", None),
        index=snapshot_index(parsed.entries),
        parser_variant=parsed.variant,
        top_level_shape=parsed.top_level_shape,
        empty_untrusted=empty_untrusted,
    )
