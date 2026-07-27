from __future__ import annotations

from dataclasses import dataclass

from config import UNDERGRAD_EXCLUSIVE_ACTIONS, PluginSettings, parse_numeric_ids
from data_source.students import PendingRequest
from onebot.member_info import is_user_in_group
from storage.audit_log import utc_now_iso

UNDERGRAD_EXCLUSIVE_SUGGESTION = "该 QQ 已在其他本科目标群，请管理员人工确认"
UNDERGRAD_EXCLUSIVE_AUTO_SUGGESTION = (
    "该 QQ 已在其他本科目标群，已按多群互斥策略拒绝"
)


def normalize_undergrad_exclusive_action(value: str) -> str:
    action = str(value or "manual_review").strip().lower()
    if action not in UNDERGRAD_EXCLUSIVE_ACTIONS:
        return "manual_review"
    return action


def build_undergrad_exclusive_qq_reject_reason(settings: PluginSettings) -> str:
    return (
        (settings.undergrad_exclusive_reject_reason or "").strip()
        or "不可加入多个群"
    )


def build_undergrad_exclusive_reason(settings: PluginSettings) -> str:
    reject_reason = build_undergrad_exclusive_qq_reject_reason(settings)
    return (
        "申请人 QQ 已在本科新生群之一，不进入自动/批量放行；"
        f"如确认不可加入多个群，请使用 /audit no <编号> {reject_reason} confirm"
    )


def is_undergrad_exclusive_decision(
    decision,
    pending: PendingRequest | None = None,
) -> bool:
    parsed = (pending.parsed if pending is not None else {}) or {}
    if parsed.get("_undergrad_exclusive_hit"):
        return True
    reason = getattr(decision, "reason", "") or ""
    return "已在本科新生群" in reason or "已在其他本科目标群" in reason


def is_undergrad_exclusive_auto_reject(
    decision,
    pending: PendingRequest | None = None,
) -> bool:
    parsed = (pending.parsed if pending is not None else {}) or {}
    if not parsed.get("_undergrad_exclusive_hit"):
        return False
    action = parsed.get("_undergrad_exclusive_action") or "manual_review"
    return action == "auto_reject" and getattr(decision, "decision", None) == "reject"


@dataclass
class UndergradExclusiveHit:
    hit: bool
    group_ids: list[str]
    checked_group_ids: list[str]
    failed_group_ids: list[str]
    message: str


def resolve_undergrad_exclusive_group_ids(settings: PluginSettings) -> frozenset[str]:
    raw = (settings.undergrad_exclusive_group_ids or "").strip()
    if raw:
        return parse_numeric_ids(raw, "undergrad_exclusive_group_ids")
    return settings.target_group_ids


async def check_undergrad_exclusive_membership(
    actions,
    settings: PluginSettings,
    *,
    current_group_id: str,
    user_id: str,
) -> UndergradExclusiveHit:
    if not settings.undergrad_exclusive_groups_enabled:
        return UndergradExclusiveHit(
            hit=False,
            group_ids=[],
            checked_group_ids=[],
            failed_group_ids=[],
            message="disabled",
        )

    all_groups = resolve_undergrad_exclusive_group_ids(settings)
    other_groups = sorted(g for g in all_groups if g != current_group_id)
    if not other_groups:
        return UndergradExclusiveHit(
            hit=False,
            group_ids=[],
            checked_group_ids=[],
            failed_group_ids=[],
            message="no_other_groups",
        )

    hit_groups: list[str] = []
    failed_groups: list[str] = []
    for group_id in other_groups:
        try:
            result = await actions.get_group_member_info(
                group_id, user_id, no_cache=True
            )
        except Exception:
            failed_groups.append(group_id)
            continue

        if not getattr(result, "ok", False):
            failed_groups.append(group_id)
            continue

        present = is_user_in_group(result)
        if present is True:
            hit_groups.append(group_id)
        elif present is None:
            failed_groups.append(group_id)

    if hit_groups:
        groups_text = "、".join(hit_groups)
        return UndergradExclusiveHit(
            hit=True,
            group_ids=hit_groups,
            checked_group_ids=other_groups,
            failed_group_ids=failed_groups,
            message=f"已在本科目标群：{groups_text}",
        )

    if failed_groups and len(failed_groups) == len(other_groups):
        return UndergradExclusiveHit(
            hit=False,
            group_ids=[],
            checked_group_ids=other_groups,
            failed_group_ids=failed_groups,
            message="all_checks_failed",
        )

    return UndergradExclusiveHit(
        hit=False,
        group_ids=[],
        checked_group_ids=other_groups,
        failed_group_ids=failed_groups,
        message="not_in_other_groups",
    )


def apply_undergrad_exclusive_hit(
    decision,
    parsed_dict: dict,
    hit: UndergradExclusiveHit,
    settings: PluginSettings,
) -> dict:
    parsed_dict = dict(parsed_dict or {})
    parsed_dict["_undergrad_exclusive_hit"] = True
    parsed_dict["_undergrad_exclusive_group_ids"] = list(hit.group_ids)
    parsed_dict["_undergrad_exclusive_checked_group_ids"] = list(hit.checked_group_ids)
    action = normalize_undergrad_exclusive_action(settings.undergrad_exclusive_action)
    parsed_dict["_undergrad_exclusive_action"] = action
    decision.should_auto_approve = False
    if action == "auto_reject":
        decision.decision = "reject"
        decision.reason = (
            "申请人 QQ 已在其他本科目标群，已按多群互斥策略自动拒绝"
        )
        decision.suggestion = UNDERGRAD_EXCLUSIVE_AUTO_SUGGESTION
    else:
        decision.decision = "manual_review"
        decision.reason = build_undergrad_exclusive_reason(settings)
        decision.suggestion = UNDERGRAD_EXCLUSIVE_SUGGESTION
    return parsed_dict


async def filter_releasable_for_undergrad_exclusive(
    pipeline,
    settings: PluginSettings,
    requests_store,
    items: list[PendingRequest],
    *,
    audit_log=None,
) -> tuple[list[PendingRequest], int]:
    if not settings.undergrad_exclusive_groups_enabled:
        return items, 0

    log = audit_log or pipeline.audit
    kept: list[PendingRequest] = []
    blocked = 0
    for req in items:
        if getattr(req, "profile", "undergraduate") != "undergraduate":
            kept.append(req)
            continue

        hit = await check_undergrad_exclusive_membership(
            pipeline.actions,
            settings,
            current_group_id=req.group_id,
            user_id=req.user_id,
        )
        if hit.hit:
            blocked += 1
            reason = build_undergrad_exclusive_reason(settings)
            parsed = dict(req.parsed or {})
            parsed["_undergrad_exclusive_hit"] = True
            parsed["_undergrad_exclusive_group_ids"] = list(hit.group_ids)
            parsed["_undergrad_exclusive_checked_group_ids"] = list(
                hit.checked_group_ids
            )
            parsed["_undergrad_exclusive_action"] = "manual_review"
            await requests_store.update_by_id(
                req.id,
                {
                    "decision": "manual_review",
                    "reason": reason,
                    "parsed": parsed,
                    "updated_at": utc_now_iso(),
                },
            )
            await log.append(
                {
                    "type": "batch_preflight_undergrad_exclusive_blocked",
                    "request_id": req.id,
                    "group_id": req.group_id,
                    "user_id": req.user_id,
                    "hit_group_ids": hit.group_ids,
                }
            )
            continue

        if hit.failed_group_ids:
            await log.append(
                {
                    "type": "undergrad_exclusive_check_partial_failed",
                    "request_id": req.id,
                    "group_id": req.group_id,
                    "user_id": req.user_id,
                    "failed_group_ids": hit.failed_group_ids,
                    "checked_group_ids": hit.checked_group_ids,
                }
            )
        kept.append(req)
    return kept, blocked
