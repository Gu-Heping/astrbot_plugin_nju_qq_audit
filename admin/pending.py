from __future__ import annotations

from admin.ctx_compat import ensure_ctx_compat


async def fetch_pending_for_admin(
    ctx, admin_id: str, limit: int = 10
) -> tuple[list, dict[int, str]]:
    ensure_ctx_compat(ctx)
    limit = max(1, min(int(limit), 50))
    items = await ctx.requests.list_pending(limit=limit)
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
