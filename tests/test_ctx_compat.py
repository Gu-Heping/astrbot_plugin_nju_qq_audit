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
