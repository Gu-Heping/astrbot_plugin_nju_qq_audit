from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from admin.labels import applicant_summary
from config import PluginSettings
from core.normalize import has_non_grade26_keyword, is_grade26_student_id
from core.pipeline import RematchSummary
from data_source.student_cache import SyncState
from data_source.students import PendingRequest


@dataclass
class ReleaseItemPreview:
    index: int
    request_id: str
    summary: str
    group_id: str
    comment: str


@dataclass
class ReleasePreview:
    items: list[ReleaseItemPreview]
    total_releasable: int
    rematch: RematchSummary | None = None


@dataclass
class ReleaseLineResult:
    index: int
    request_id: str
    summary: str
    ok: bool
    message: str


@dataclass
class ReleaseResult:
    requested: int
    processed: int
    success: int
    failed: int
    remaining: int
    lines: list[ReleaseLineResult] = field(default_factory=list)
    cancelled: bool = False
    rematch: RematchSummary | None = None


@dataclass
class CatchupPreview:
    sync_ok: bool
    sync_message: str
    sync_state: SyncState | None = None
    rematch: RematchSummary | None = None
    release_preview: ReleasePreview | None = None


@dataclass
class CatchupResult:
    sync_ok: bool
    sync_message: str
    sync_state: SyncState | None = None
    rematch: RematchSummary | None = None
    release: ReleaseResult | None = None
    busy: bool = False


def _effective_student_id(req: PendingRequest) -> str | None:
    parsed = req.parsed or {}
    sid = parsed.get("student_id")
    if sid:
        return str(sid)
    match = req.match or {}
    matched = match.get("matched_student_id")
    if matched:
        return str(matched)
    return None


def _is_grade26_releasable(req: PendingRequest) -> bool:
    sid = _effective_student_id(req)
    if not sid:
        return False
    return is_grade26_student_id(sid)


def is_releasable(req: PendingRequest, settings: PluginSettings) -> bool:
    if getattr(req, "profile", "undergraduate") == "graduate":
        return False
    if req.status != "pending" or req.processed_at:
        return False
    if req.decision != "approve":
        return False
    if req.match_strength != "strong":
        return False
    if req.group_id not in settings.target_group_ids:
        return False
    if not req.flag:
        return False
    if req.sub_type != "add":
        return False
    if has_non_grade26_keyword(req.comment or ""):
        return False

    if not _is_grade26_releasable(req):
        return False

    return True


async def list_releasable(
    requests_store,
    settings: PluginSettings,
    *,
    limit: int | None = None,
) -> list[PendingRequest]:
    pending = await requests_store.list_pending(limit=1000)
    releasable = [r for r in pending if is_releasable(r, settings)]
    releasable.sort(key=lambda r: r.created_at)
    if limit is not None:
        return releasable[:limit]
    return releasable


async def rematch_and_list_releasable(
    pipeline,
    requests_store,
    settings: PluginSettings,
    *,
    source: str,
    limit: int | None = None,
) -> tuple[RematchSummary, list[PendingRequest]]:
    pending_before = await requests_store.list_pending(limit=1000)
    before_ids = {r.id for r in pending_before if is_releasable(r, settings)}
    summary = await pipeline.rematch_active_pending(
        source=source, profiles=frozenset({"undergraduate"})
    )
    items = await list_releasable(requests_store, settings, limit=limit)
    summary.newly_releasable = sum(1 for r in items if r.id not in before_ids)
    return summary, items


def build_preview(items: list[PendingRequest]) -> ReleasePreview:
    previews = []
    for idx, req in enumerate(items, start=1):
        previews.append(
            ReleaseItemPreview(
                index=idx,
                request_id=req.id,
                summary=applicant_summary(req),
                group_id=req.group_id,
                comment=(req.comment or "")[:80],
            )
        )
    return ReleasePreview(items=previews, total_releasable=len(items))


def _format_rematch_lines(rematch: RematchSummary | None) -> list[str]:
    if rematch is None:
        return []
    return [
        f"已按当前名单重算 pending：{rematch.scanned} 条",
        f"更新：{rematch.changed}，新升为 strong：{rematch.upgraded_to_strong}，"
        f"新可放行：{rematch.newly_releasable}",
    ]


def format_release_help(count: int, settings: PluginSettings) -> str:
    interval_sec = settings.batch_approve_interval_ms / 1000
    return "\n".join(
        [
            "分批通过（临时放行历史 strong match 申请）",
            "",
            f"当前可通过：{count} 条",
            f"单次上限：{settings.batch_approve_max_count} 条",
            f"间隔：{interval_sec:g} 秒",
            "",
            "命令：",
            "/audit release preview        预览（会先按当前缓存重算 pending）",
            "/audit release 10 confirm     通过最多 10 条",
            "/audit release all confirm    通过最多上限条数",
            "",
            "同步名单并补放：",
            "/audit catchup preview        拉最新名单 + 重算 + 预览",
            "/audit catchup confirm        拉最新名单 + 重算 + 放行（上限内）",
            "",
            "说明：",
            "- 仅 strong match + 26级 + 目标群 + pending",
            "- 不改变当前运行模式（不是长期 auto）",
            "- 建议先发欢迎消息，再分批执行",
            "- 校对表更新后优先使用 /audit catchup preview",
        ]
    )


def format_catchup_help(settings: PluginSettings) -> str:
    interval_sec = settings.batch_approve_interval_ms / 1000
    return "\n".join(
        [
            "catchup：同步 NJUTable 名单 → 重算 pending → 补放 strong",
            "",
            f"单次上限：{settings.batch_approve_max_count} 条",
            f"间隔：{interval_sec:g} 秒",
            "",
            "命令：",
            "/audit catchup preview        同步 + 重算 + 预览（不放人）",
            "/audit catchup confirm        同步 + 重算 + 放行最多上限条",
            "/audit catchup 10 confirm     同步 + 重算 + 放行最多 10 条",
            "",
            "说明：",
            "- 同步失败时不会重算或放行",
            "- 仅放行 strong + 26 级 + 目标群 pending",
            "- 不改变当前运行模式",
        ]
    )


def format_release_preview(preview: ReleasePreview, settings: PluginSettings) -> str:
    lines: list[str] = []
    lines.extend(_format_rematch_lines(preview.rematch))
    if lines:
        lines.append("")
    if not preview.items:
        lines.append("当前没有可分批通过的 strong match 申请。")
        return "\n".join(lines)
    lines.extend(
        [
            f"可分批通过：{preview.total_releasable} 条（预览）",
            f"条件：strong match + 26级 + 目标群 + pending",
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
    lines.append("执行：/audit release 10 confirm")
    return "\n".join(lines)


def format_release_result(result: ReleaseResult, settings: PluginSettings) -> str:
    lines: list[str] = []
    rematch_lines = _format_rematch_lines(result.rematch)
    if rematch_lines:
        lines.extend(rematch_lines)
        lines.append("")

    if result.cancelled:
        prefix = "分批通过已取消"
    elif result.processed == 0 and result.requested == 0:
        if lines:
            lines.append("没有可分批通过的申请。")
            return "\n".join(lines)
        return "没有可分批通过的申请。"
    else:
        prefix = f"准备分批通过 {result.requested} 条申请"

    lines.extend(
        [
            prefix,
            f"间隔：{settings.batch_approve_interval_ms / 1000:g} 秒",
            "条件：仅 strong match + 26级 + 目标群 + pending",
            "",
        ]
    )
    if result.lines:
        lines.append("正在处理：")
        for line in result.lines:
            status = "成功" if line.ok else f"失败：{line.message}"
            lines.append(f"[{line.index}] {line.summary} ... {status}")
        lines.append("")
    lines.extend(
        [
            "完成：",
            f"成功：{result.success}",
            f"失败：{result.failed}",
            f"剩余可通过：{result.remaining}",
            "",
            "建议：",
            "管理员可以发送欢迎消息后，再执行 /audit release 10 confirm",
        ]
    )
    return "\n".join(lines)


def _format_sync_header(sync_ok: bool, sync_message: str, sync_state: SyncState | None) -> list[str]:
    if not sync_ok:
        return [
            f"名单同步失败：{sync_message}",
            "未对 pending 重算或放行。请先 /audit sync status 排查。",
        ]
    if sync_state is not None:
        cached = sync_state.filtered_count or sync_state.row_count
        return [
            f"名单同步：成功，缓存 {cached} 人"
            f"（filtered {sync_state.filtered_count}，source={sync_state.source}）",
        ]
    return [f"名单同步：成功。{sync_message}"]


def format_catchup_preview(preview: CatchupPreview, settings: PluginSettings) -> str:
    lines = _format_sync_header(preview.sync_ok, preview.sync_message, preview.sync_state)
    if not preview.sync_ok:
        return "\n".join(lines)
    lines.append("")
    rematch = preview.rematch
    if rematch is not None:
        lines.append(
            f"重算 pending：共 {rematch.scanned} 条，新升为 strong：{rematch.upgraded_to_strong}，"
            f"本次可放行：{(preview.release_preview.total_releasable if preview.release_preview else 0)}"
        )
        lines.append("")
    rp = preview.release_preview
    if rp is None or not rp.items:
        lines.append("当前没有可补放的 strong match 申请。")
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
    lines.append("执行：/audit catchup confirm")
    return "\n".join(lines)


def format_catchup_result(result: CatchupResult, settings: PluginSettings) -> str:
    if result.busy:
        return "已有分批任务进行中，请稍后再试。"
    lines = _format_sync_header(result.sync_ok, result.sync_message, result.sync_state)
    if not result.sync_ok:
        return "\n".join(lines)
    lines.append("")
    if result.rematch is not None:
        lines.extend(_format_rematch_lines(result.rematch))
        lines.append("")
    if result.release is None:
        lines.append("没有可分批通过的申请。")
        return "\n".join(lines)
    # Avoid duplicating rematch block from format_release_result
    release_copy = ReleaseResult(
        requested=result.release.requested,
        processed=result.release.processed,
        success=result.release.success,
        failed=result.release.failed,
        remaining=result.release.remaining,
        lines=result.release.lines,
        cancelled=result.release.cancelled,
        rematch=None,
    )
    lines.append(format_release_result(release_copy, settings))
    return "\n".join(lines)


def _is_sync_failure(message: str) -> bool:
    text = (message or "").strip()
    return text.startswith("同步失败")


class ReleaseService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._running = False
        self._cancel = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._running

    def request_cancel(self) -> None:
        self._cancel.set()

    async def preview(
        self,
        requests_store,
        settings: PluginSettings,
        *,
        pipeline=None,
        rematch_source: str = "release_preview",
    ) -> ReleasePreview:
        max_count = settings.batch_approve_max_count
        rematch: RematchSummary | None = None
        if pipeline is not None:
            rematch, items = await rematch_and_list_releasable(
                pipeline,
                requests_store,
                settings,
                source=rematch_source,
                limit=max_count,
            )
        else:
            items = await list_releasable(requests_store, settings, limit=max_count)
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
        rematch_source: str | None = "release_batch",
        skip_rematch: bool = False,
    ) -> ReleaseResult | None:
        if not await self._try_begin():
            return None
        try:
            rematch: RematchSummary | None = None
            if not skip_rematch and rematch_source and pipeline is not None:
                rematch, _ = await rematch_and_list_releasable(
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
        cache,
    ) -> CatchupPreview:
        sync_message = await run_sync(source="catchup")
        if _is_sync_failure(sync_message):
            return CatchupPreview(
                sync_ok=False,
                sync_message=sync_message,
                sync_state=cache.load_sync_state(),
            )
        sync_state = cache.load_sync_state()
        rematch, items = await rematch_and_list_releasable(
            pipeline,
            requests_store,
            settings,
            source="catchup_preview",
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
        cache,
        admin_user_id: str,
        count: int | None,
        audit_log=None,
    ) -> CatchupResult:
        sync_message = await run_sync(source="catchup")
        if _is_sync_failure(sync_message):
            return CatchupResult(
                sync_ok=False,
                sync_message=sync_message,
                sync_state=cache.load_sync_state(),
            )
        sync_state = cache.load_sync_state()
        release = await self.run_batch(
            requests_store=requests_store,
            pipeline=pipeline,
            settings=settings,
            admin_user_id=admin_user_id,
            count=count,
            audit_log=audit_log,
            rematch_source="catchup_batch",
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

    async def _try_begin(self) -> bool:
        async with self._lock:
            if self._running:
                return False
            self._running = True
            self._cancel.clear()
            return True

    async def _finish(self) -> None:
        async with self._lock:
            self._running = False

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
        all_releasable = await list_releasable(requests_store, settings)
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

        interval = settings.batch_approve_interval_ms / 1000.0
        for idx, req in enumerate(batch, start=1):
            if self._cancel.is_set():
                result.cancelled = True
                break
            if not is_releasable(req, settings):
                continue

            action = await pipeline.admin_approve(req, admin_user_id)
            result.processed += 1
            line = ReleaseLineResult(
                index=idx,
                request_id=req.id,
                summary=applicant_summary(req),
                ok=action.ok,
                message="" if action.ok else (action.message or "未知错误"),
            )
            result.lines.append(line)
            if action.ok:
                result.success += 1
            else:
                result.failed += 1

            if audit_log is not None:
                await audit_log.append(
                    {
                        "type": "batch_release",
                        "request_id": req.id,
                        "admin_user_id": admin_user_id,
                        "ok": action.ok,
                        "message": action.message if not action.ok else "ok",
                    }
                )

            if idx < len(batch) and not self._cancel.is_set():
                await asyncio.sleep(interval)

        remaining_all = await list_releasable(requests_store, settings)
        result.remaining = len(remaining_all)
        return result
