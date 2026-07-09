from __future__ import annotations

from storage.list_cache import AdminListCacheStore


def ensure_ctx_compat(ctx) -> None:
    """Patch ctx after hot-reload when initialize() did not rebuild PluginContext."""
    if not hasattr(ctx, "list_cache"):
        ctx.list_cache = AdminListCacheStore(ctx.data_dir / "list_cache.json")
        if getattr(ctx, "notifier", None) is not None:
            ctx.notifier.list_cache = ctx.list_cache
