import asyncio

import pytest

from config import load_settings
from data_source.student_cache import StudentCache, SyncState
from data_source.sync_scheduler import SyncScheduler, _resolve_failure_result


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


@pytest.mark.asyncio
async def test_scheduler_not_start_for_mock(tmp_path):
    settings = load_settings(
        DummyConfig({"student_source": "mock", "auto_sync_enabled": True})
    )
    cache = StudentCache(tmp_path)
    scheduler = SyncScheduler()
    await scheduler.start(settings, cache, lambda: asyncio.sleep(0))
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_interval_clamped_in_status(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "student_source": "nju_table",
                "auto_sync_interval_minutes": 3,
            }
        )
    )
    scheduler = SyncScheduler()
    text = scheduler.format_status(settings, SyncState())
    assert "auto_sync_interval_minutes: 10" in text


@pytest.mark.asyncio
async def test_sync_lock_prevents_concurrent(tmp_path):
    cache = StudentCache(tmp_path)
    scheduler = SyncScheduler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_sync():
        started.set()
        await release.wait()
        return "同步成功: source=mock, rows=1, filtered=1"

    task = asyncio.create_task(scheduler.run_once(slow_sync, cache, source="manual"))
    await started.wait()
    second = await scheduler.run_once(lambda: asyncio.sleep(0), cache, source="manual")
    assert "正在进行" in second
    release.set()
    await task


@pytest.mark.asyncio
async def test_busy_does_not_overwrite_previous_success(tmp_path):
    cache = StudentCache(tmp_path)
    cache.save_sync_state(
        SyncState(
            last_sync_at="2026-07-14T00:00:00+00:00",
            last_sync_result="success",
            last_sync_source="manual",
            filtered_count=695,
        )
    )
    scheduler = SyncScheduler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_sync():
        started.set()
        await release.wait()
        return "同步成功: source=mock, filtered=1"

    async def noop():
        return "同步成功: should-not-run"

    task = asyncio.create_task(scheduler.run_once(slow_sync, cache, source="auto"))
    await started.wait()
    busy = await scheduler.run_once(noop, cache, source="auto")
    assert "正在进行" in busy
    state = cache.load_sync_state()
    # Still the pre-busy success until first task finishes
    assert state.last_sync_result == "success"
    assert state.last_sync_source == "manual"
    assert state.last_sync_at == "2026-07-14T00:00:00+00:00"
    release.set()
    await task
    state = cache.load_sync_state()
    assert state.last_sync_result == "success"
    assert state.last_sync_source == "auto"


@pytest.mark.asyncio
async def test_run_once_with_execute_body_succeeds_as_auto(tmp_path):
    """Correct wiring: scheduler holds lock, callback must not call run_once again."""
    cache = StudentCache(tmp_path)
    scheduler = SyncScheduler()
    calls = {"n": 0}

    async def execute_sync():
        calls["n"] += 1
        state = cache.load_sync_state()
        state.last_sync_at = "2026-07-14T10:00:00+00:00"
        state.last_sync_result = "success"
        state.filtered_count = 839
        cache.save_sync_state(state)
        return "同步成功: source=nju_table, raw=900, mapped=900, filtered=839"

    msg = await scheduler.run_once(execute_sync, cache, source="auto")
    assert msg.startswith("同步成功")
    assert calls["n"] == 1
    state = cache.load_sync_state()
    assert state.last_sync_result == "success"
    assert state.last_sync_source == "auto"
    assert state.filtered_count == 839


@pytest.mark.asyncio
async def test_nested_run_sync_style_callback_does_not_fake_success(tmp_path):
    """Old buggy wiring: callback itself calls run_once → busy; must not claim success."""
    cache = StudentCache(tmp_path)
    cache.save_sync_state(
        SyncState(
            last_sync_at="2026-07-13T06:34:56+00:00",
            last_sync_result="success",
            last_sync_source="manual",
            filtered_count=695,
        )
    )
    scheduler = SyncScheduler()

    async def nested_like_old_run_sync():
        return await scheduler.run_once(
            lambda: asyncio.sleep(0, result="同步成功: should-not"),
            cache,
            source="manual",
        )

    msg = await scheduler.run_once(nested_like_old_run_sync, cache, source="auto")
    assert "正在进行" in msg
    state = cache.load_sync_state()
    assert state.last_sync_result == "success"
    assert state.last_sync_source == "manual"
    assert state.filtered_count == 695


@pytest.mark.asyncio
async def test_failure_preserves_exception_name(tmp_path):
    cache = StudentCache(tmp_path)
    cache.save_sync_state(
        SyncState(last_sync_result="failed: TimeoutError", filtered_count=10)
    )
    scheduler = SyncScheduler()

    async def fail_sync():
        return "同步失败: TimeoutError。已保留旧缓存 10 条。"

    msg = await scheduler.run_once(fail_sync, cache, source="auto")
    assert "同步失败" in msg
    state = cache.load_sync_state()
    assert state.last_sync_result == "failed: TimeoutError"
    assert state.last_sync_source == "auto"


@pytest.mark.asyncio
async def test_failure_parses_exception_from_message(tmp_path):
    cache = StudentCache(tmp_path)
    scheduler = SyncScheduler()

    async def fail_sync():
        return "同步失败: ClientConnectorError。已保留旧缓存 695 条。"

    await scheduler.run_once(fail_sync, cache, source="auto")
    state = cache.load_sync_state()
    assert state.last_sync_result == "failed: ClientConnectorError"


def test_resolve_failure_result_helpers():
    assert (
        _resolve_failure_result(
            "同步失败: TimeoutError。已保留旧缓存 1 条。",
            "failed: TimeoutError",
        )
        == "failed: TimeoutError"
    )
    assert (
        _resolve_failure_result("同步失败: RuntimeError。x", None)
        == "failed: RuntimeError"
    )
    assert _resolve_failure_result("其它错误", None) == "failed"


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_task(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "student_source": "nju_table",
                "auto_sync_enabled": True,
                "auto_sync_interval_minutes": 600,
            }
        )
    )
    cache = StudentCache(tmp_path)
    scheduler = SyncScheduler()
    await scheduler.start(settings, cache, lambda: asyncio.sleep(0))
    assert scheduler._task is not None
    await scheduler.stop()
    assert scheduler._task is None
