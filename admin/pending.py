from __future__ import annotations

from admin.command_resolver import sanitize_action_message
from admin.ctx_compat import ensure_ctx_compat
from admin.labels import applicant_summary


def format_stale_list(items: list, index_map: dict[int, str]) -> str:
    if not items:
        return "目前没有 stale 申请。"
    lines = [f"stale 申请：{len(items)} 条", ""]
    for idx, item in enumerate(items, start=1):
        public = item.to_public_dict()
        summary = applicant_summary(item)
        comment = (public.get("comment") or "")[:80]
        last_action = public.get("last_action_result") or {}
        reason = sanitize_action_message(last_action.get("message"))
        lines.extend(
            [
                f"[{idx}] {summary}",
                f"群：{public.get('group_id', '')}",
                f"验证：{comment or '（空）'}",
                f"原因：{reason}",
            ]
        )
        lines.append(f"/audit view {idx}  |  /audit restore {idx} confirm")
        lines.append(f"/audit mark-external {idx} confirm")
        lines.append("")
    lines.append("编号来自本次 /audit stale 列表，30 分钟内有效。")
    return "\n".join(lines)


async def fetch_pending_for_admin(
    ctx, admin_id: str, limit: int = 10, *, profile: str | None = None
) -> tuple[list, dict[int, str]]:
    ensure_ctx_compat(ctx)
    limit = max(1, min(int(limit), 50))
    items = await ctx.requests.list_pending(limit=1000)
    if profile in {"undergraduate", "graduate"}:
        items = [
            item
            for item in items
            if (getattr(item, "profile", None) or "undergraduate") == profile
        ]
    items = items[:limit]
    index_map = await ctx.list_cache.refresh(admin_id, [item.id for item in items])
    return items, index_map


async def fetch_stale_for_admin(
    ctx, admin_id: str, limit: int = 10
) -> tuple[list, dict[int, str]]:
    ensure_ctx_compat(ctx)
    limit = max(1, min(int(limit), 50))
    items = await ctx.requests.list_stale(limit=limit)
    cache_key = f"{admin_id}:stale"
    index_map = await ctx.list_cache.refresh(cache_key, [item.id for item in items])
    return items, index_map
