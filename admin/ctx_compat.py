from __future__ import annotations

from storage.list_cache import AdminListCacheStore


def ensure_ctx_compat(ctx) -> None:
    """Patch ctx after hot-reload when initialize() did not rebuild PluginContext."""
    if not hasattr(ctx, "list_cache"):
        ctx.list_cache = AdminListCacheStore(ctx.data_dir / "list_cache.json")
        if getattr(ctx, "notifier", None) is not None:
            ctx.notifier.list_cache = ctx.list_cache
    if not hasattr(ctx, "list_pending_for_admin"):

        async def _bound(admin_id: str, limit: int = 10):
            from admin.pending import fetch_pending_for_admin

            return await fetch_pending_for_admin(ctx, admin_id, limit)

        ctx.list_pending_for_admin = _bound  # type: ignore[method-assign]
