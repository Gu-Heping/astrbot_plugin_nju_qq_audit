from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from admin.labels import applicant_summary
from core.pipeline import RematchSummary
from data_source.students import PendingRequest

if TYPE_CHECKING:
    from core.pipeline import AuditPipeline
    from storage.audit_log import AuditLog
    from storage.list_cache import AdminListCacheStore
    from storage.requests_store import RequestsStore

SWEEP_LARGE_THRESHOLD = 100


def _strength(req: PendingRequest) -> str:
    return req.match_strength or (req.match or {}).get("strength") or "none"


def is_active_pending(req: PendingRequest) -> bool:
    return req.status == "pending" and not req.processed_at


def _is_undergraduate(req: PendingRequest) -> bool:
    return (getattr(req, "profile", None) or "undergraduate") == "undergraduate"


def is_sweep_candidate(req: PendingRequest) -> bool:
    """Non-strong undergrad pending — safe to locally dismiss in auto mode.

    Graduate pendings are excluded so undergraduate maintenance cannot close them.
    """
    return (
        _is_undergraduate(req)
        and is_active_pending(req)
        and _strength(req) != "strong"
    )


def is_kept_strong(req: PendingRequest) -> bool:
    return (
        _is_undergraduate(req)
        and is_active_pending(req)
        and _strength(req) == "strong"
    )


@dataclass
class SweepPreview:
    candidates: list[PendingRequest]
    kept_strong: list[PendingRequest]
    rematch: RematchSummary | None = None


@dataclass
class SweepResult:
    reason: str
    dismissed: int = 0
    idempotent: int = 0
    skipped_strong: int = 0
    skipped_terminal: int = 0
    failed: int = 0
    rematch: RematchSummary | None = None
    sample_ids: list[str] = field(default_factory=list)


def parse_sweep_command(message_str: str, arg1: str = "", arg2: str = "") -> tuple[str, str]:
    """Parse `/audit sweep` args.

    Returns (action, reason) where action is one of:
      help | preview | confirm | need_reason | bad_usage
    """
    text = (message_str or "").strip()
    a1 = (arg1 or "").strip()
    a2 = (arg2 or "").strip()

    if not a1:
        return "help", ""

    if a1 == "preview":
        return "preview", ""

    if a1 == "confirm":
        # Prefer full message so multi-word reasons survive AstrBot arg splitting.
        reason = ""
        for prefix in ("/audit sweep confirm", "sweep confirm"):
            idx = text.find(prefix)
            if idx >= 0:
                reason = text[idx + len(prefix) :].strip()
                break
        if not reason:
            reason = a2
        if not reason:
            return "need_reason", ""
        return "confirm", reason

    return "bad_usage", ""


async def collect_sweep_preview(pipeline: AuditPipeline) -> SweepPreview:
    rematch = await pipeline.rematch_active_pending(
        source="sweep", profiles=frozenset({"undergraduate"})
    )
    pending = await pipeline.requests.list_pending(limit=1000)
    candidates = [r for r in pending if is_sweep_candidate(r)]
    kept = [r for r in pending if is_kept_strong(r)]
    candidates.sort(key=lambda r: r.created_at or "")
    kept.sort(key=lambda r: r.created_at or "")
    return SweepPreview(candidates=candidates, kept_strong=kept, rematch=rematch)


def format_sweep_help() -> str:
    return "\n".join(
        [
            "sweep：本地批量关闭非 strong pending（不调 QQ）",
            "",
            "命令：",
            "/audit sweep preview          预览将关闭的非 strong",
            "/audit sweep confirm <原因>   一键 dismiss（原因必填）",
            "",
            "说明：",
            "- 仅关闭本科 match_strength ≠ strong 的 pending",
            "- 保留 strong，留给 auto / release / catchup",
            "- 不处理研究生 pending（避免本科维护误关）",
            "- 不调用 QQ；适合「别人已在 QQ 拒绝但 bot 未收到事件」",
            "- 仍在 QQ 等待审核要用 /audit no；已入群用 /audit mark-external",
            "- 建议先 /audit sweep preview",
        ]
    )


def _format_rematch_lines(rematch: RematchSummary | None) -> list[str]:
    if rematch is None:
        return []
    return [
        f"重算：扫描 {rematch.scanned}，变更 {rematch.changed}，升级 strong {rematch.upgraded_to_strong}",
    ]


def format_sweep_preview(preview: SweepPreview) -> str:
    lines: list[str] = []
    lines.extend(_format_rematch_lines(preview.rematch))
    if lines:
        lines.append("")

    n = len(preview.candidates)
    kept = len(preview.kept_strong)
    lines.extend(
        [
            f"将本地关闭（non-strong）：{n} 条",
            f"将保留（strong）：{kept} 条",
        ]
    )
    if n > SWEEP_LARGE_THRESHOLD:
        lines.append(f"注意：候选超过 {SWEEP_LARGE_THRESHOLD} 条，confirm 仍会全部关闭。")

    if not preview.candidates:
        lines.extend(
            [
                "",
                "当前没有可 sweep 的非 strong pending。",
                "仍可用 /audit list 查看 strong，或 /audit release / catchup。",
            ]
        )
        return "\n".join(lines)

    lines.append("")
    lines.append("样例（最多 10 条）：")
    for i, req in enumerate(preview.candidates[:10], start=1):
        strength = _strength(req)
        lines.append(
            f"{i}. {applicant_summary(req)} | {strength} | {req.decision} | {req.id}"
        )
    if n > 10:
        lines.append(f"... 另有 {n - 10} 条")

    lines.extend(
        [
            "",
            "确认关闭请发送：",
            "/audit sweep confirm <原因>",
            "例如：/audit sweep confirm QQ侧管理员已拒或长期无效",
        ]
    )
    return "\n".join(lines)


def format_sweep_result(result: SweepResult) -> str:
    lines: list[str] = []
    lines.extend(_format_rematch_lines(result.rematch))
    if lines:
        lines.append("")
    lines.extend(
        [
            "sweep 完成（本地 dismiss，未调 QQ）",
            f"原因：{result.reason}",
            f"已关闭：{result.dismissed}",
            f"幂等跳过：{result.idempotent}",
            f"保留 strong：{result.skipped_strong}",
            f"已终态跳过：{result.skipped_terminal}",
            f"失败：{result.failed}",
        ]
    )
    if result.sample_ids:
        lines.append("样例 ID：" + ", ".join(result.sample_ids[:5]))
    return "\n".join(lines)


async def run_sweep(
    *,
    pipeline: AuditPipeline,
    admin_user_id: str,
    reason: str,
    list_cache: AdminListCacheStore | None = None,
    audit_log: AuditLog | None = None,
) -> SweepResult:
    reason = (reason or "").strip()
    preview = await collect_sweep_preview(pipeline)
    result = SweepResult(
        reason=reason,
        skipped_strong=len(preview.kept_strong),
        rematch=preview.rematch,
    )
    if not reason:
        result.failed = len(preview.candidates)
        return result

    for req in preview.candidates:
        outcome = await pipeline.dismiss_pending(
            req,
            admin_user_id,
            reason,
            list_cache=list_cache,
        )
        if outcome.get("ok") and outcome.get("idempotent"):
            result.idempotent += 1
        elif outcome.get("ok"):
            result.dismissed += 1
            if len(result.sample_ids) < 5:
                result.sample_ids.append(req.id)
        elif outcome.get("already_terminal"):
            result.skipped_terminal += 1
        else:
            result.failed += 1

    # Recount strong still pending after dismisses (should be unchanged).
    pending_after = await pipeline.requests.list_pending(limit=1000)
    result.skipped_strong = sum(1 for r in pending_after if is_kept_strong(r))

    log = audit_log or getattr(pipeline, "audit", None)
    if log is not None:
        payload: dict[str, Any] = {
            "type": "bulk_dismiss_non_strong",
            "admin_user_id": admin_user_id,
            "reason": reason,
            "dismissed": result.dismissed,
            "idempotent": result.idempotent,
            "skipped_strong": result.skipped_strong,
            "skipped_terminal": result.skipped_terminal,
            "failed": result.failed,
            "candidate_count": len(preview.candidates),
        }
        await log.append(payload)

    return result
