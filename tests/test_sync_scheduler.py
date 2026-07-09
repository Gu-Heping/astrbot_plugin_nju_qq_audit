import asyncio

import pytest

from config import load_settings
from data_source.student_cache import StudentCache, SyncState
from data_source.sync_scheduler import SyncScheduler


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
