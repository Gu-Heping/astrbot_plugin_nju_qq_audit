from __future__ import annotations

from dataclasses import dataclass

from config import PluginSettings
from data_source.students import PendingRequest
from storage.audit_log import utc_now_iso


@dataclass
class UndergradOverflowHit:
    hit: bool
    source_group_id: str
    redirect_group_id: str
    member_count: int | None
    threshold: int
    failed: bool
    message: str


def _parse_member_count(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def overflow_config_valid(settings: PluginSettings) -> bool:
    if not settings.undergrad_overflow_enabled:
        return False
    if settings.undergrad_overflow_threshold <= 0:
        return False
    source = (settings.undergrad_overflow_source_group_id or "").strip()
    redirect = (settings.undergrad_overflow_redirect_group_id or "").strip()
    if not source or not redirect or source == redirect:
        return False
    return True


async def check_undergrad_overflow(
    actions,
    settings: PluginSettings,
    *,
    current_group_id: str,
) -> UndergradOverflowHit:
    source = (settings.undergrad_overflow_source_group_id or "").strip()
    redirect = (settings.undergrad_overflow_redirect_group_id or "").strip()
    threshold = int(settings.undergrad_overflow_threshold or 0)

    if not overflow_config_valid(settings):
        return UndergradOverflowHit(
            hit=False,
            source_group_id=source,
            redirect_group_id=redirect,
            member_count=None,
            threshold=threshold,
            failed=False,
            message="disabled_or_invalid_config",
        )

    if current_group_id != source:
        return UndergradOverflowHit(
            hit=False,
            source_group_id=source,
            redirect_group_id=redirect,
            member_count=None,
            threshold=threshold,
            failed=False,
            message="not_source_group",
        )

    try:
        result = await actions.get_group_info(current_group_id, no_cache=True)
    except Exception:
        return UndergradOverflowHit(
            hit=False,
            source_group_id=source,
            redirect_group_id=redirect,
            member_count=None,
            threshold=threshold,
            failed=True,
            message="api_exception",
        )

    if not getattr(result, "ok", False):
        return UndergradOverflowHit(
            hit=False,
            source_group_id=source,
            redirect_group_id=redirect,
            member_count=None,
            threshold=threshold,
            failed=True,
            message=getattr(result, "message", None) or "api_failed",
        )

    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return UndergradOverflowHit(
            hit=False,
            source_group_id=source,
            redirect_group_id=redirect,
            member_count=None,
            threshold=threshold,
            failed=True,
            message="invalid_payload",
        )

    member_count = _parse_member_count(data.get("member_count"))
    if member_count is None:
        return UndergradOverflowHit(
            hit=False,
            source_group_id=source,
            redirect_group_id=redirect,
            member_count=None,
            threshold=threshold,
            failed=True,
            message="missing_member_count",
        )

    if member_count >= threshold:
        return UndergradOverflowHit(
            hit=True,
            source_group_id=source,
            redirect_group_id=redirect,
            member_count=member_count,
            threshold=threshold,
            failed=False,
            message=f"member_count={member_count}>={threshold}",
        )

    return UndergradOverflowHit(
        hit=False,
        source_group_id=source,
        redirect_group_id=redirect,
        member_count=member_count,
        threshold=threshold,
        failed=False,
        message="below_threshold",
    )


def build_undergrad_overflow_reject_reason(
    settings: PluginSettings,
    hit: UndergradOverflowHit,
) -> str:
    template = (
        settings.undergrad_overflow_reject_reason_template or ""
    ).strip() or "当前群人数较多，请申请加入 {redirect_group_id} 群"
    redirect = hit.redirect_group_id or settings.undergrad_overflow_redirect_group_id
    try:
        return template.format(
            source_group_id=hit.source_group_id,
            redirect_group_id=redirect,
            member_count=hit.member_count if hit.member_count is not None else "",
            threshold=hit.threshold,
        )
    except Exception:
        return f"当前群人数较多，请申请加入 {redirect} 群"


def apply_undergrad_overflow_hit(
    decision,
    parsed_dict: dict,
    hit: UndergradOverflowHit,
    settings: PluginSettings,
) -> dict:
    parsed_dict = dict(parsed_dict or {})
    parsed_dict["_undergrad_overflow_hit"] = True
    parsed_dict["_undergrad_overflow_source_group_id"] = hit.source_group_id
    parsed_dict["_undergrad_overflow_redirect_group_id"] = hit.redirect_group_id
    parsed_dict["_undergrad_overflow_member_count"] = hit.member_count
    parsed_dict["_undergrad_overflow_threshold"] = hit.threshold
    decision.decision = "reject"
    decision.should_auto_approve = False
    decision.reason = build_undergrad_overflow_reject_reason(settings, hit)
    decision.suggestion = "当前本科目标群接近满员，已引导申请人去备用群"
    return parsed_dict


def is_undergrad_overflow_decision(decision, pending: PendingRequest | None = None) -> bool:
    parsed = (pending.parsed if pending is not None else {}) or {}
    if parsed.get("_undergrad_overflow_hit"):
        return True
    reason = getattr(decision, "reason", "") or ""
    redirect = (parsed.get("_undergrad_overflow_redirect_group_id") or "").strip()
    if redirect and redirect in reason:
        return True
    return "请申请加入" in reason and "群" in reason


def overflow_reject_reason_from_parsed(
    parsed: dict | None,
    settings: PluginSettings,
) -> str:
    parsed = parsed or {}
    hit = UndergradOverflowHit(
        hit=True,
        source_group_id=str(parsed.get("_undergrad_overflow_source_group_id") or ""),
        redirect_group_id=str(parsed.get("_undergrad_overflow_redirect_group_id") or ""),
        member_count=parsed.get("_undergrad_overflow_member_count"),
        threshold=int(parsed.get("_undergrad_overflow_threshold") or settings.undergrad_overflow_threshold),
        failed=False,
        message="stored",
    )
    return build_undergrad_overflow_reject_reason(settings, hit)


async def filter_releasable_for_undergrad_overflow(
    pipeline,
    settings: PluginSettings,
    requests_store,
    items: list[PendingRequest],
    *,
    audit_log=None,
) -> tuple[list[PendingRequest], int]:
    if not overflow_config_valid(settings):
        return items, 0

    log = audit_log or pipeline.audit
    kept: list[PendingRequest] = []
    blocked = 0
    for req in items:
        if getattr(req, "profile", "undergraduate") != "undergraduate":
            kept.append(req)
            continue
        if req.group_id != settings.undergrad_overflow_source_group_id:
            kept.append(req)
            continue

        hit = await check_undergrad_overflow(
            pipeline.actions,
            settings,
            current_group_id=req.group_id,
        )
        if hit.hit:
            blocked += 1
            reason = build_undergrad_overflow_reject_reason(settings, hit)
            parsed = dict(req.parsed or {})
            parsed["_undergrad_overflow_hit"] = True
            parsed["_undergrad_overflow_source_group_id"] = hit.source_group_id
            parsed["_undergrad_overflow_redirect_group_id"] = hit.redirect_group_id
            parsed["_undergrad_overflow_member_count"] = hit.member_count
            parsed["_undergrad_overflow_threshold"] = hit.threshold
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
                    "type": "batch_preflight_undergrad_overflow_blocked",
                    "request_id": req.id,
                    "group_id": req.group_id,
                    "user_id": req.user_id,
                    "member_count": hit.member_count,
                    "threshold": hit.threshold,
                }
            )
            continue

        if hit.failed:
            await log.append(
                {
                    "type": "undergrad_overflow_check_failed",
                    "request_id": req.id,
                    "group_id": req.group_id,
                    "user_id": req.user_id,
                    "message": hit.message,
                }
            )
        kept.append(req)
    return kept, blocked
