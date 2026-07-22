from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from admin.labels import applicant_summary
from admin.release import (
    CatchupPreview,
    CatchupResult,
    ReleaseItemPreview,
    ReleaseLineResult,
    ReleasePreview,
    ReleaseResult,
    ReleaseService,
    build_preview,
    preflight_releasable_with_live_snapshot,
)
from config import PluginSettings
from core.pipeline import RematchSummary
from data_source.student_cache import SyncState
from data_source.students import PendingRequest
from graduate.cache import GraduateStudentCache


def _format_rematch_lines(rematch: RematchSummary | None) -> list[str]:
    if rematch is None:
        return []
    return [
        f"已按当前名单重算待处理：{rematch.scanned} 条",
        f"更新：{rematch.changed}，新升为强匹配：{rematch.upgraded_to_strong}，"
        f"新可放行：{rematch.newly_releasable}",
    ]


def is_grad_releasable(req: PendingRequest, settings: PluginSettings) -> bool:
    if getattr(req, "profile", "undergraduate") != "graduate":
        return False
    if req.status != "pending" or req.processed_at:
        return False
    if req.decision != "approve":
        return False
    if req.match_strength != "strong":
        return False
    if req.group_id not in settings.grad_target_group_ids:
        return False
    if not req.flag:
        return False
    if req.sub_type != "add":
        return False

    match = req.match or {}
    if match.get("candidate_count") != 1:
        return False

    parsed = req.parsed or {}
    if not parsed.get("name"):
        return False
    if parsed.get("admission_type") not in {"硕士", "博士"}:
        return False
    major_ok = bool(
        parsed.get("major_text")
        or parsed.get("major_code_candidates")
        or match.get("major_name")
    )
    if not major_ok:
        return False

    errors = parsed.get("parse_errors") or []
    if "ai_parse_merged" in errors and not settings.ai_parse_allow_auto_approve:
        return False

    return True


async def list_grad_releasable(
    requests_store,
    settings: PluginSettings,
    *,
    limit: int | None = None,
) -> list[PendingRequest]:
    pending = await requests_store.list_pending(limit=1000)
    releasable = [r for r in pending if is_grad_releasable(r, settings)]
    releasable.sort(key=lambda r: r.created_at)
    if limit is not None:
        return releasable[:limit]
    return releasable


async def rematch_and_list_grad_releasable(
    pipeline,
    requests_store,
    settings: PluginSettings,
    *,
    source: str,
    limit: int | None = None,
) -> tuple[RematchSummary, list[PendingRequest]]:
    pending_before = await requests_store.list_pending(limit=1000)
    before_ids = {r.id for r in pending_before if is_grad_releasable(r, settings)}
    summary = await pipeline.rematch_active_pending(
        source=source, profiles=frozenset({"graduate"})
    )
    items = await list_grad_releasable(requests_store, settings, limit=limit)
    summary.newly_releasable = sum(1 for r in items if r.id not in before_ids)
    return summary, items


def _is_grad_sync_failure(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return True
    if text.startswith("研究生名单同步失败"):
        return True
    if text.startswith("同步失败"):
        return True
    if "同步正在进行中" in text:
        return True
    return False


def _format_grad_sync_header(
    sync_ok: bool, sync_message: str, sync_state: SyncState | None
) -> list[str]:
    if not sync_ok:
        msg = (sync_message or "").strip()
        if msg.startswith("研究生名单同步失败"):
            lines = msg.splitlines()
        else:
            lines = [f"研究生名单同步失败：{msg}"]
        lines.append("未对待处理重算或放行。请先 /audit sync grad 排查。")
        return lines
    if sync_state is not None:
        cached = sync_state.filtered_count or sync_state.row_count
        return [
            f"研究生名单同步：成功，缓存 {cached} 人"
            f"（filtered {sync_state.filtered_count}，source={sync_state.source}）",
        ]
    return [f"研究生名单同步：成功。{sync_message}"]


def format_grad_release_help(count: int, settings: PluginSettings) -> str:
    interval_sec = settings.batch_approve_interval_ms / 1000
    return "\n".join(
        [
            "研究生强匹配批量放行（临时放行历史强匹配申请）",
            "",
            f"当前可通过：{count} 条",
            f"单次上限：{settings.batch_approve_max_count} 条",
            f"间隔：{interval_sec:g} 秒",
            "",
            "命令：",
            "/audit release grad preview        预览（会先按当前缓存重算待处理）",
            "/audit release grad 10 confirm     通过最多 10 条",
            "/audit release grad all confirm    通过最多上限条数",
            "",
            "同步名单并补放：",
            "/audit catchup grad preview        拉最新研究生名单 + 重算 + 预览",
            "/audit catchup grad confirm        拉最新研究生名单 + 重算 + 放行（上限内）",
            "",
            "筛选条件（须同时满足）：",
            "- 研究生申请",
            "- 系统强匹配",
            "- 姓名 + 专业/代码 + 硕或博 唯一匹配",
            "- 仍在 QQ 待处理队列中",
            "",
            "说明：",
            "- 不改变当前运行模式（不是长期自动审核）",
            "- 不处理本科；本科请用 /audit release / catchup",
            "- 别名：/audit grad-release …、/audit grad-catchup …",
        ]
    )


def format_grad_catchup_help(settings: PluginSettings) -> str:
    interval_sec = settings.batch_approve_interval_ms / 1000
    return "\n".join(
        [
            "研究生补放：同步名单 → 重算待处理 → 补放强匹配",
            "",
            f"单次上限：{settings.batch_approve_max_count} 条",
            f"间隔：{interval_sec:g} 秒",
            "",
            "命令：",
            "/audit catchup grad preview        同步 + 重算 + 预览（不放人）",
            "/audit catchup grad confirm        同步 + 重算 + 放行最多上限条",
            "/audit catchup grad 10 confirm     同步 + 重算 + 放行最多 10 条",
            "",
            "筛选条件（须同时满足）：",
            "- 研究生申请",
            "- 系统强匹配",
            "- 姓名 + 专业/代码 + 硕或博 唯一匹配",
            "- 仍在 QQ 待处理队列中",
            "",
            "说明：",
            "- 同步失败时不会重算或放行",
            "- 不改变当前运行模式",
            "- 不处理本科；本科请用 /audit catchup",
        ]
    )


def format_grad_release_preview(preview: ReleasePreview, settings: PluginSettings) -> str:
    lines: list[str] = []
    lines.extend(_format_rematch_lines(preview.rematch))
    if lines:
        lines.append("")
    if not preview.items:
        lines.append("当前没有可分批通过的研究生强匹配申请。")
        return "\n".join(lines)
    lines.extend(
        [
            f"研究生强匹配批量放行（预览）：{preview.total_releasable} 条",
            "筛选条件：研究生申请 · 系统强匹配 · 姓名+专业/代码+硕或博唯一匹配 · 仍在待处理队列中",
            f"间隔：{settings.batch_approve_interval_ms / 1000:g} 秒",
            "",
        ]
    )
    for item in preview.items:
        lines.extend(
            [
                f"[{item.index}] {item.summary}",
                f"群：{item.group_id}",
                f"验证：{item.comment or '（空）'}",
                "",
            ]
        )
    lines.append("执行：/audit release grad 10 confirm")
    return "\n".join(lines)


def format_grad_release_result(result: ReleaseResult, settings: PluginSettings) -> str:
    lines: list[str] = []
    rematch_lines = _format_rematch_lines(result.rematch)
    if rematch_lines:
        lines.extend(rematch_lines)
        lines.append("")

    if result.cancelled:
        prefix = "研究生强匹配批量放行已取消"
    elif result.processed == 0 and result.requested == 0:
        if lines:
            lines.append("没有可分批通过的研究生申请。")
            return "\n".join(lines)
        return "没有可分批通过的研究生申请。"
    else:
        prefix = f"准备研究生强匹配批量放行 {result.requested} 条"

    lines.extend(
        [
            prefix,
            f"间隔：{settings.batch_approve_interval_ms / 1000:g} 秒",
            "筛选条件：研究生申请 · 系统强匹配 · 姓名+专业/代码+硕或博唯一匹配 · 仍在待处理队列中",
            "",
        ]
    )
    if result.lines:
        lines.append("正在处理：")
        for line in result.lines:
            status = "成功" if line.ok else f"失败：{line.message}"
            if line.final_status == "stale":
                status = "QQ 侧已无此申请，已移出队列"
            elif line.final_status == "external":
                status = "用户已在群内，已标记外部通过"
            elif line.final_status == "skipped":
                status = f"已跳过：{line.message}"
            lines.append(f"[{line.index}] {line.summary} ... {status}")
        lines.append("")
    lines.extend(
        [
            "完成：",
            f"成功：{result.success}",
            f"已失效：{result.stale_count}",
            f"外部已入群：{result.external_count}",
            f"失败：{result.failed}",
            f"剩余可通过：{result.remaining}",
            "",
            "建议：",
            "管理员可以发送欢迎消息后，再执行 /audit release grad 10 confirm",
        ]
    )
    return "\n".join(lines)


def format_grad_catchup_preview(preview: CatchupPreview, settings: PluginSettings) -> str:
    lines = _format_grad_sync_header(
        preview.sync_ok, preview.sync_message, preview.sync_state
    )
    if not preview.sync_ok:
        return "\n".join(lines)
    lines.append("")
    rematch = preview.rematch
    if rematch is not None:
        lines.append(
            f"重算待处理：共 {rematch.scanned} 条，新升为强匹配：{rematch.upgraded_to_strong}，"
            f"本次可放行：{(preview.release_preview.total_releasable if preview.release_preview else 0)}"
        )
        lines.append("")
    rp = preview.release_preview
    if rp is None or not rp.items:
        lines.append("当前没有可补放的研究生强匹配申请。")
        return "\n".join(lines)
    for item in rp.items:
        lines.extend(
            [
                f"[{item.index}] {item.summary}",
                f"群：{item.group_id}",
                f"验证：{item.comment or '（空）'}",
                "",
            ]
        )
    lines.append("执行：/audit catchup grad confirm")
    return "\n".join(lines)


def format_grad_catchup_result(result: CatchupResult, settings: PluginSettings) -> str:
    if result.busy:
        return "已有分批任务进行中，请稍后再试。"
    lines = _format_grad_sync_header(
        result.sync_ok, result.sync_message, result.sync_state
    )
    if not result.sync_ok:
        return "\n".join(lines)
    lines.append("")
    if result.rematch is not None:
        lines.extend(_format_rematch_lines(result.rematch))
        lines.append("")
    if result.release is None:
        lines.append("没有可分批通过的研究生申请。")
        return "\n".join(lines)
    release_copy = ReleaseResult(
        requested=result.release.requested,
        processed=result.release.processed,
        success=result.release.success,
        failed=result.release.failed,
        remaining=result.release.remaining,
        stale_count=result.release.stale_count,
        external_count=result.release.external_count,
        skipped_count=result.release.skipped_count,
        lines=result.release.lines,
        cancelled=result.release.cancelled,
        rematch=None,
    )
    lines.append(format_grad_release_result(release_copy, settings))
    return "\n".join(lines)


class GradReleaseService:
    """Graduate strong-match batch release; shares mutex with undergrad ReleaseService."""

    def __init__(self, share_with: ReleaseService | None = None) -> None:
        self._owner = share_with
        if share_with is None:
            self._lock = asyncio.Lock()
            self._running = False
            self._cancel = asyncio.Event()

    @property
    def is_running(self) -> bool:
        if self._owner is not None:
            return self._owner.is_running
        return self._running

    def request_cancel(self) -> None:
        if self._owner is not None:
            self._owner.request_cancel()
        else:
            self._cancel.set()

    async def _try_begin(self) -> bool:
        if self._owner is not None:
            return await self._owner._try_begin()
        async with self._lock:
            if self._running:
                return False
            self._running = True
            self._cancel.clear()
            return True

    async def _finish(self) -> None:
        if self._owner is not None:
            await self._owner._finish()
            return
        async with self._lock:
            self._running = False

    def _cancel_requested(self) -> bool:
        if self._owner is not None:
            return self._owner._cancel.is_set()
        return self._cancel.is_set()

    async def preview(
        self,
        requests_store,
        settings: PluginSettings,
        *,
        pipeline=None,
        rematch_source: str = "grad_release_preview",
    ) -> ReleasePreview:
        max_count = settings.batch_approve_max_count
        rematch: RematchSummary | None = None
        if pipeline is not None:
            rematch, items = await rematch_and_list_grad_releasable(
                pipeline,
                requests_store,
                settings,
                source=rematch_source,
                limit=max_count,
            )
        else:
            items = await list_grad_releasable(requests_store, settings, limit=max_count)
        preview = build_preview(items)
        preview.rematch = rematch
        return preview

    async def run_batch(
        self,
        *,
        requests_store,
        pipeline,
        settings: PluginSettings,
        admin_user_id: str,
        count: int | None,
        audit_log=None,
        rematch_source: str | None = "grad_release_batch",
        skip_rematch: bool = False,
    ) -> ReleaseResult | None:
        if not await self._try_begin():
            return None
        try:
            rematch: RematchSummary | None = None
            if not skip_rematch and rematch_source and pipeline is not None:
                rematch, _ = await rematch_and_list_grad_releasable(
                    pipeline,
                    requests_store,
                    settings,
                    source=rematch_source,
                )
            result = await self._run_batch_unlocked(
                requests_store=requests_store,
                pipeline=pipeline,
                settings=settings,
                admin_user_id=admin_user_id,
                count=count,
                audit_log=audit_log,
            )
            result.rematch = rematch
            return result
        finally:
            await self._finish()

    async def catchup_preview(
        self,
        *,
        run_sync: Callable[..., Awaitable[str]],
        pipeline,
        requests_store,
        settings: PluginSettings,
        grad_cache: GraduateStudentCache,
    ) -> CatchupPreview:
        sync_message = await run_sync(source="grad_catchup")
        if _is_grad_sync_failure(sync_message):
            return CatchupPreview(
                sync_ok=False,
                sync_message=sync_message,
                sync_state=grad_cache.load_sync_state(),
            )
        sync_state = grad_cache.load_sync_state()
        rematch, items = await rematch_and_list_grad_releasable(
            pipeline,
            requests_store,
            settings,
            source="grad_catchup_preview",
            limit=settings.batch_approve_max_count,
        )
        release_preview = build_preview(items)
        release_preview.rematch = rematch
        return CatchupPreview(
            sync_ok=True,
            sync_message=sync_message,
            sync_state=sync_state,
            rematch=rematch,
            release_preview=release_preview,
        )

    async def catchup_batch(
        self,
        *,
        run_sync: Callable[..., Awaitable[str]],
        pipeline,
        requests_store,
        settings: PluginSettings,
        grad_cache: GraduateStudentCache,
        admin_user_id: str,
        count: int | None,
        audit_log=None,
    ) -> CatchupResult:
        sync_message = await run_sync(source="grad_catchup")
        if _is_grad_sync_failure(sync_message):
            return CatchupResult(
                sync_ok=False,
                sync_message=sync_message,
                sync_state=grad_cache.load_sync_state(),
            )
        sync_state = grad_cache.load_sync_state()
        release = await self.run_batch(
            requests_store=requests_store,
            pipeline=pipeline,
            settings=settings,
            admin_user_id=admin_user_id,
            count=count,
            audit_log=audit_log,
            rematch_source="grad_catchup_batch",
        )
        if release is None:
            return CatchupResult(
                sync_ok=True,
                sync_message=sync_message,
                sync_state=sync_state,
                busy=True,
            )
        return CatchupResult(
            sync_ok=True,
            sync_message=sync_message,
            sync_state=sync_state,
            rematch=release.rematch,
            release=release,
        )

    async def _run_batch_unlocked(
        self,
        *,
        requests_store,
        pipeline,
        settings: PluginSettings,
        admin_user_id: str,
        count: int | None,
        audit_log,
    ) -> ReleaseResult:
        all_releasable = await list_grad_releasable(requests_store, settings)
        if count is None:
            limit = settings.batch_approve_max_count
        else:
            limit = min(count, settings.batch_approve_max_count)
        batch = all_releasable[:limit]

        result = ReleaseResult(
            requested=len(batch),
            processed=0,
            success=0,
            failed=0,
            remaining=max(0, len(all_releasable) - len(batch)),
            lines=[],
        )

        preflight_batch = await preflight_releasable_with_live_snapshot(
            pipeline, requests_store, settings, batch
        )
        preflight_ids = {req.id for req in preflight_batch}
        index_by_id = {req.id: idx for idx, req in enumerate(batch, start=1)}
        summary_by_id = {req.id: applicant_summary(req) for req in batch}
        for req in batch:
            if req.id in preflight_ids:
                continue
            latest = await requests_store.get_by_id(req.id)
            if latest is None:
                continue
            if latest.status == "stale":
                result.processed += 1
                result.stale_count += 1
                result.lines.append(
                    ReleaseLineResult(
                        index=index_by_id[req.id],
                        request_id=req.id,
                        summary=summary_by_id[req.id],
                        ok=True,
                        message="QQ 侧已无此申请，已移出队列",
                        final_status="stale",
                    )
                )
            elif latest.status == "external":
                result.processed += 1
                result.external_count += 1
                result.lines.append(
                    ReleaseLineResult(
                        index=index_by_id[req.id],
                        request_id=req.id,
                        summary=summary_by_id[req.id],
                        ok=True,
                        message="用户已在群内，已标记外部通过",
                        final_status="external",
                    )
                )
            elif latest.status == "pending":
                result.processed += 1
                result.skipped_count += 1
                result.lines.append(
                    ReleaseLineResult(
                        index=index_by_id[req.id],
                        request_id=req.id,
                        summary=summary_by_id[req.id],
                        ok=True,
                        message="QQ 侧待处理匹配不唯一，保留 pending",
                        final_status="skipped",
                    )
                )

        interval = settings.batch_approve_interval_ms / 1000.0
        for position, req in enumerate(preflight_batch, start=1):
            idx = index_by_id.get(req.id, position)
            if self._cancel_requested():
                result.cancelled = True
                break
            if not is_grad_releasable(req, settings):
                continue

            action = await pipeline.admin_approve(req, admin_user_id)
            latest = await requests_store.get_by_id(req.id)
            final_status = "success"
            line_ok = action.ok
            message = "" if action.ok else (action.message or "未知错误")
            if not action.ok and latest is not None and latest.status == "stale":
                final_status = "stale"
                line_ok = True
                message = "QQ 侧已无此申请，已移出队列"
            elif not action.ok and latest is not None and latest.status == "external":
                final_status = "external"
                line_ok = True
                message = "用户已在群内，已标记外部通过"
            result.processed += 1
            line = ReleaseLineResult(
                index=idx,
                request_id=req.id,
                summary=summary_by_id.get(req.id, applicant_summary(req)),
                ok=line_ok,
                final_status=final_status,
                message="" if action.ok else (action.message or "未知错误"),
            )
            if final_status != "success":
                line.message = message
            result.lines.append(line)
            if final_status == "stale":
                result.stale_count += 1
            elif final_status == "external":
                result.external_count += 1
            elif action.ok:
                result.success += 1
            else:
                result.failed += 1

            if audit_log is not None:
                await audit_log.append(
                    {
                        "type": "batch_grad_release",
                        "request_id": req.id,
                        "admin_user_id": admin_user_id,
                        "ok": line_ok,
                        "message": message if not line_ok else final_status,
                    }
                )

            if position < len(preflight_batch) and not self._cancel_requested():
                await asyncio.sleep(interval)

        remaining_all = await list_grad_releasable(requests_store, settings)
        result.remaining = len(remaining_all)
        return result


# Re-export preview item type for typing convenience in tests.
__all__ = [
    "GradReleaseService",
    "ReleaseItemPreview",
    "format_grad_catchup_help",
    "format_grad_catchup_preview",
    "format_grad_catchup_result",
    "format_grad_release_help",
    "format_grad_release_preview",
    "format_grad_release_result",
    "is_grad_releasable",
    "list_grad_releasable",
    "rematch_and_list_grad_releasable",
]
