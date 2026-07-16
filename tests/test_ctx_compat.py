from pathlib import Path

from admin.ctx_compat import ensure_ctx_compat


class LegacyCtx:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


def test_ensure_ctx_compat_adds_release_and_sync(tmp_path):
    ctx = LegacyCtx(tmp_path)
    ensure_ctx_compat(ctx)
    assert hasattr(ctx, "release_service")
    assert hasattr(ctx, "sync_scheduler")
    assert ctx.release_service.is_running is False


def test_ensure_ctx_compat_backfills_graduate_sync(tmp_path):
    from config import load_settings

    class DummyConfig(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class OldCtx:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.settings = load_settings(DummyConfig({}))
            self.cache = object()
            self._http_session = None

        async def execute_sync(self, *, source: str = "manual") -> str:
            return "同步成功: source=mock"

    ctx = OldCtx(tmp_path)
    ensure_ctx_compat(ctx)
    assert hasattr(ctx, "grad_cache")
    assert hasattr(ctx, "execute_grad_sync")
    assert hasattr(ctx, "run_grad_sync")
    assert hasattr(ctx, "run_sync")
    assert ctx.grad_cache.cache_path.name == "grad_students.cache.json"
