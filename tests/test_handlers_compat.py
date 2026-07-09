from admin.ctx_compat import ensure_ctx_compat
from storage.list_cache import AdminListCacheStore


class LegacyCtx:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.notifier = type("N", (), {"list_cache": None})()


def test_ensure_ctx_compat_adds_list_cache(tmp_path):
    ctx = LegacyCtx(tmp_path)
    assert not hasattr(ctx, "list_cache")
    ensure_ctx_compat(ctx)
    assert isinstance(ctx.list_cache, AdminListCacheStore)
    assert ctx.notifier.list_cache is ctx.list_cache


def test_ensure_ctx_compat_idempotent(tmp_path):
    ctx = LegacyCtx(tmp_path)
    ensure_ctx_compat(ctx)
    cache = ctx.list_cache
    ensure_ctx_compat(ctx)
    assert ctx.list_cache is cache
