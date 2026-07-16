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
    if not hasattr(ctx, "release_service"):
        from admin.release import ReleaseService

        ctx.release_service = ReleaseService()
    if not hasattr(ctx, "sync_scheduler"):
        from data_source.sync_scheduler import SyncScheduler

        ctx.sync_scheduler = SyncScheduler()
    if not hasattr(ctx, "_grad_sync_lock"):
        import asyncio

        ctx._grad_sync_lock = asyncio.Lock()
    if not hasattr(ctx, "grad_cache"):
        from graduate.cache import GraduateStudentCache

        ctx.grad_cache = GraduateStudentCache(ctx.data_dir)
        pipeline = getattr(ctx, "pipeline", None)
        if pipeline is not None and getattr(pipeline, "grad_cache", None) is None:
            pipeline.grad_cache = ctx.grad_cache
    if not hasattr(ctx, "run_sync"):
        async def _run_sync(*, source: str = "manual") -> str:
            async def _locked() -> str:
                return await ctx.execute_sync(source=source)

            return await ctx.sync_scheduler.run_once(
                _locked,
                ctx.cache,
                source=source,
            )

        ctx.run_sync = _run_sync  # type: ignore[method-assign]
    if not hasattr(ctx, "execute_grad_sync"):
        async def _execute_grad_sync(*, source: str = "manual") -> str:
            import aiohttp

            from graduate.njutable_provider import sync_graduate_students

            session = getattr(ctx, "_http_session", None) or aiohttp.ClientSession()
            own = getattr(ctx, "_http_session", None) is None
            try:
                state = await sync_graduate_students(
                    ctx.settings, ctx.grad_cache, session
                )
                state.last_sync_source = source
                ctx.grad_cache.save_sync_state(state)
                return (
                    f"研究生同步成功: source={state.source}, "
                    f"raw={state.raw_row_count or state.row_count}, "
                    f"mapped={state.mapped_count or state.row_count}, "
                    f"filtered={state.filtered_count}"
                )
            except Exception as exc:
                cached = ctx.grad_cache.load_students()
                return (
                    f"研究生同步失败: {type(exc).__name__}。"
                    f"已保留旧缓存 {len(cached)} 条。"
                )
            finally:
                if own:
                    await session.close()

        ctx.execute_grad_sync = _execute_grad_sync  # type: ignore[method-assign]
    if not hasattr(ctx, "run_grad_sync"):
        async def _run_grad_sync(*, source: str = "manual") -> str:
            if ctx._grad_sync_lock.locked():
                return "研究生同步正在进行中，请稍后再试。"
            async with ctx._grad_sync_lock:
                return await ctx.execute_grad_sync(source=source)

        ctx.run_grad_sync = _run_grad_sync  # type: ignore[method-assign]
