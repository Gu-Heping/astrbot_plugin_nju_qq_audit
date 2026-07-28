from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from admin.labels import applicant_summary
from config import PluginSettings
from core.undergrad_overflow import (
    UndergradOverflowHit,
    build_undergrad_overflow_reject_reason,
    overflow_config_valid,
    overflow_reject_reason_from_parsed,
)
from data_source.students import PendingRequest


@dataclass
class OverflowCleanupSample:
    request_id: str
    user_id: str
    summary: str
    comment: str
    flag: str


@dataclass
class OverflowCleanupPreview:
    group_id: str
    pending_count: int
    eligible_count: int
    reason: str
    samples: list[OverflowCleanupSample] = field(default_factory=list)
    config_ok: bool = True
    message: str = ""


@dataclass
class OverflowCleanupLineResult:
    request_id: str
    summary: str
    ok: bool
    message: str
    final_status: str = ""


@dataclass
class OverflowCleanupResult:
    requested: int
    success: int
    failed: int
    lines: list[OverflowCleanupLineResult] = field(default_factory=list)
    busy: bool = False
    message: str = ""


def is_overflow_cleanup_candidate(
    req: PendingRequest, settings: PluginSettings
) -> bool:
    if not overflow_config_valid(settings):
        return False
    source = (settings.undergrad_overflow_source_group_id or "").strip()
    if req.status != "pending":
        return False
    if req.group_id != source:
        return False
    if req.sub_type != "add":
        return False
    if not (req.flag or "").strip():
        return False
    profile = getattr(req, "profile", None) or (req.parsed or {}).get("_profile") or "undergraduate"
    return profile == "undergraduate"


def resolve_overflow_cleanup_reason(
    settings: PluginSettings, req: PendingRequest
) -> str:
    parsed = req.parsed or {}
    if parsed.get("_undergrad_overflow_hit") or parsed.get(
        "_undergrad_overflow_redirect_group_id"
    ):
        return overflow_reject_reason_from_parsed(parsed, settings)
    hit = UndergradOverflowHit(
        hit=True,
        source_group_id=(settings.undergrad_overflow_source_group_id or "").strip(),
        redirect_group_id=(settings.undergrad_overflow_redirect_group_id or "").strip(),
        member_count=parsed.get("_undergrad_overflow_member_count"),
        threshold=int(
            parsed.get("_undergrad_overflow_threshold")
            or settings.undergrad_overflow_threshold
            or 0
        ),
        failed=False,
        message="overflow_cleanup",
    )
    return build_undergrad_overflow_reject_reason(settings, hit)


async def list_overflow_cleanup_candidates(
    requests_store, settings: PluginSettings
) -> list[PendingRequest]:
    items = await requests_store.list_all()
    eligible = [req for req in items if is_overflow_cleanup_candidate(req, settings)]
    eligible.sort(key=lambda r: r.created_at)
    return eligible


def _count_pending_in_source_group(
    items: list[PendingRequest], settings: PluginSettings
) -> int:
    source = (settings.undergrad_overflow_source_group_id or "").strip()
    count = 0
    for req in items:
        if req.status != "pending":
            continue
        if req.group_id != source:
            continue
        count += 1
    return count


def _build_samples(
    items: list[PendingRequest], *, limit: int = 3
) -> list[OverflowCleanupSample]:
    samples: list[OverflowCleanupSample] = []
    for req in items[:limit]:
        samples.append(
            OverflowCleanupSample(
                request_id=req.id,
                user_id=req.user_id,
                summary=applicant_summary(req),
                comment=(req.comment or "")[:80],
                flag=req.flag,
            )
        )
    return samples


class OverflowCleanupService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def preview(
        self, requests_store, settings: PluginSettings
    ) -> OverflowCleanupPreview:
        source = (settings.undergrad_overflow_source_group_id or "").strip()
        if not overflow_config_valid(settings):
            return OverflowCleanupPreview(
                group_id=source,
                pending_count=0,
                eligible_count=0,
                reason="",
                config_ok=False,
                message="本科 overflow 配置无效或未启用",
            )
        all_items = await requests_store.list_all()
        pending_count = _count_pending_in_source_group(all_items, settings)
        eligible = [
            req for req in all_items if is_overflow_cleanup_candidate(req, settings)
        ]
        reason = ""
        if eligible:
            reason = resolve_overflow_cleanup_reason(settings, eligible[0])
        elif source:
            reason = resolve_overflow_cleanup_reason(
                settings,
                PendingRequest(
                    id="preview",
                    group_id=source,
                    user_id="0",
                    comment="",
                    flag="preview",
                    sub_type="add",
                    decision="reject",
                    confidence=0,
                    reason="",
                    mode="manual",
                    status="pending",
                    created_at="1970-01-01T00:00:00+00:00",
                    parsed={
                        "_undergrad_overflow_source_group_id": source,
                        "_undergrad_overflow_redirect_group_id": (
                            settings.undergrad_overflow_redirect_group_id or ""
                        ),
                        "_undergrad_overflow_threshold": settings.undergrad_overflow_threshold,
                    },
                ),
            )
        return OverflowCleanupPreview(
            group_id=source,
            pending_count=pending_count,
            eligible_count=len(eligible),
            reason=reason,
            samples=_build_samples(eligible),
        )

    async def confirm(
        self,
        *,
        requests_store,
        pipeline,
        settings: PluginSettings,
        admin_user_id: str,
        audit_log=None,
        list_cache=None,
    ) -> OverflowCleanupResult:
        if not overflow_config_valid(settings):
            return OverflowCleanupResult(
                requested=0,
                success=0,
                failed=0,
                message="本科 overflow 配置无效或未启用",
            )
        if not await self._try_begin():
            return OverflowCleanupResult(
                requested=0,
                success=0,
                failed=0,
                busy=True,
                message="overflow cleanup 正在执行，请勿重复触发",
            )
        try:
            items = await list_overflow_cleanup_candidates(requests_store, settings)
            result = OverflowCleanupResult(
                requested=len(items),
                success=0,
                failed=0,
            )
            log = audit_log or pipeline.audit
            for req in items:
                summary = applicant_summary(req)
                try:
                    reason = resolve_overflow_cleanup_reason(settings, req)
                    action_result, final_status = await pipeline._dispatch_reject(
                        req,
                        reason=reason,
                        source="overflow_cleanup",
                        admin_user_id=admin_user_id,
                        admin_command="overflow_cleanup",
                        list_cache=list_cache,
                    )
                    ok = action_result.ok or final_status == "processed"
                    if ok:
                        result.success += 1
                    else:
                        result.failed += 1
                    result.lines.append(
                        OverflowCleanupLineResult(
                            request_id=req.id,
                            summary=summary,
                            ok=ok,
                            message=action_result.message or final_status,
                            final_status=final_status,
                        )
                    )
                except Exception as exc:
                    result.failed += 1
                    result.lines.append(
                        OverflowCleanupLineResult(
                            request_id=req.id,
                            summary=summary,
                            ok=False,
                            message=str(exc),
                            final_status="error",
                        )
                    )
            await log.append(
                {
                    "type": "overflow_cleanup_batch",
                    "admin_user_id": admin_user_id,
                    "group_id": settings.undergrad_overflow_source_group_id,
                    "requested": result.requested,
                    "success": result.success,
                    "failed": result.failed,
                }
            )
            return result
        finally:
            await self._finish()

    async def _try_begin(self) -> bool:
        async with self._lock:
            if self._running:
                return False
            self._running = True
            return True

    async def _finish(self) -> None:
        async with self._lock:
            self._running = False


def format_overflow_cleanup_preview(preview: OverflowCleanupPreview) -> str:
    if not preview.config_ok:
        return preview.message or "本科 overflow 配置无效或未启用"
    lines = [
        "Overflow 批量拒绝预览",
        "",
        f"当前群：{preview.group_id}",
        f"pending 数量：{preview.pending_count}",
        f"预计拒绝数量：{preview.eligible_count}",
        f"使用 reason：{preview.reason}",
        "",
    ]
    if not preview.samples:
        lines.append("没有符合条件的 pending 申请。")
        return "\n".join(lines)
    lines.append("示例申请：")
    for idx, sample in enumerate(preview.samples, start=1):
        lines.append(
            f"[{idx}] {sample.summary} QQ={sample.user_id} flag={sample.flag}"
        )
        if sample.comment:
            lines.append(f"    验证：{sample.comment}")
    lines.append("")
    lines.append("确认执行：/audit cleanup overflow confirm")
    return "\n".join(lines)


def format_overflow_cleanup_result(result: OverflowCleanupResult) -> str:
    if result.busy:
        return result.message or "overflow cleanup 正在执行，请勿重复触发"
    if result.message and result.requested == 0:
        return result.message
    lines = [
        "Overflow 批量拒绝结果",
        "",
        f"计划处理：{result.requested}",
        f"成功：{result.success}",
        f"失败：{result.failed}",
        "",
    ]
    if not result.lines:
        lines.append("没有处理任何申请。")
        return "\n".join(lines)
    lines.append("明细：")
    for line in result.lines[:20]:
        status = "成功" if line.ok else "失败"
        lines.append(f"- {line.summary} ({line.request_id}) {status}：{line.message}")
    if len(result.lines) > 20:
        lines.append(f"... 其余 {len(result.lines) - 20} 条省略")
    return "\n".join(lines)
