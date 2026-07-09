from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from admin.labels import applicant_summary
from config import PluginSettings
from core.normalize import has_non_grade26_keyword, is_grade26_student_id
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
            "/audit release preview        预览",
            "/audit release 10 confirm     通过最多 10 条",
            "/audit release all confirm    通过最多上限条数",
            "",
            "说明：",
            "- 仅 strong match + 26级 + 目标群 + pending",
            "- 不改变当前运行模式（不是长期 auto）",
            "- 建议先发欢迎消息，再分批执行",
        ]
    )


def format_release_preview(preview: ReleasePreview, settings: PluginSettings) -> str:
    if not preview.items:
        return "当前没有可分批通过的 strong match 申请。"
    lines = [
        f"可分批通过：{preview.total_releasable} 条（预览）",
        f"条件：strong match + 26级 + 目标群 + pending",
        f"间隔：{settings.batch_approve_interval_ms / 1000:g} 秒",
        "",
    ]
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
    if result.cancelled:
        prefix = "分批通过已取消"
    elif result.processed == 0 and result.requested == 0:
        return "没有可分批通过的申请。"
    else:
        prefix = f"准备分批通过 {result.requested} 条申请"

    lines = [
        prefix,
        f"间隔：{settings.batch_approve_interval_ms / 1000:g} 秒",
        "条件：仅 strong match + 26级 + 目标群 + pending",
        "",
    ]
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

    async def preview(self, requests_store, settings: PluginSettings) -> ReleasePreview:
        max_count = settings.batch_approve_max_count
        items = await list_releasable(requests_store, settings, limit=max_count)
        return build_preview(items)

    async def run_batch(
        self,
        *,
        requests_store,
        pipeline,
        settings: PluginSettings,
        admin_user_id: str,
        count: int | None,
        audit_log=None,
    ) -> ReleaseResult | None:
        if not await self._try_begin():
            return None
        try:
            return await self._run_batch_unlocked(
                requests_store=requests_store,
                pipeline=pipeline,
                settings=settings,
                admin_user_id=admin_user_id,
                count=count,
                audit_log=audit_log,
            )
        finally:
            await self._finish()

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
