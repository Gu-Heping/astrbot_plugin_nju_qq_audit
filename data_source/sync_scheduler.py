from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from data_source.student_cache import SyncState


_FAIL_EXC_RE = re.compile(r"同步失败:\s*([A-Za-z_][A-Za-z0-9_]*)")


def _resolve_failure_result(message: str, existing: str | None) -> str:
    """Prefer detailed failed:Exc over bare failed."""
    if existing and existing.startswith("failed:") and existing != "failed":
        return existing
    match = _FAIL_EXC_RE.search(message or "")
    if match:
        return f"failed: {match.group(1)}"
    if existing and existing.startswith("failed"):
        return existing
    return "failed"


class SyncScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._running = False
        self._cancel = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._running or (self._lock.locked())

    async def start(
        self,
        settings,
        cache,
        run_sync: Callable[[], Awaitable[str]],
        *,
        notify_on_failure: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        await self.stop()
        if settings.student_source != "nju_table":
            return
        if not settings.auto_sync_enabled and not settings.auto_sync_on_startup:
            return

        if settings.auto_sync_on_startup:
            await self.run_once(
                run_sync, cache, source="auto_startup", notify_on_failure=notify_on_failure
            )

        if settings.auto_sync_enabled:
            self._cancel.clear()
            self._task = asyncio.create_task(
                self._loop(settings, cache, run_sync, notify_on_failure)
            )

    async def stop(self) -> None:
        self._cancel.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_once(
        self,
        run_sync: Callable[[], Awaitable[str]],
        cache,
        *,
        source: str = "manual",
        notify_on_failure: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        if self._lock.locked():
            # Do not overwrite last successful sync metadata.
            return "同步正在进行中，请稍后再试。"
        async with self._lock:
            self._running = True
            try:
                message = await run_sync()
                # Nested lock mistake may return the busy string from the callback;
                # never treat that as a completed sync.
                if "正在进行" in message:
                    return message
                state = cache.load_sync_state()
                state.last_sync_source = source
                if message.startswith("同步成功"):
                    state.last_sync_result = "success"
                else:
                    state.last_sync_result = _resolve_failure_result(
                        message, state.last_sync_result
                    )
                    if notify_on_failure and "失败" in message:
                        await notify_on_failure(message)
                cache.save_sync_state(state)
                return message
            finally:
                self._running = False

    async def _loop(
        self,
        settings,
        cache,
        run_sync: Callable[[], Awaitable[str]],
        notify_on_failure,
    ) -> None:
        interval_minutes = max(10, int(settings.auto_sync_interval_minutes))
        while not self._cancel.is_set():
            next_at = datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)
            state = cache.load_sync_state()
            state.next_sync_at = next_at.isoformat()
            cache.save_sync_state(state)
            try:
                await asyncio.wait_for(self._cancel.wait(), timeout=interval_minutes * 60)
                break
            except asyncio.TimeoutError:
                pass
            if self._cancel.is_set():
                break
            await self.run_once(
                run_sync,
                cache,
                source="auto",
                notify_on_failure=notify_on_failure if settings.auto_sync_notify_admin else None,
            )

    def format_status(self, settings, sync_state: SyncState) -> str:
        return "\n".join(
            [
                "NJUTable 同步状态",
                "",
                f"auto_sync_enabled: {settings.auto_sync_enabled}",
                f"auto_sync_on_startup: {settings.auto_sync_on_startup}",
                f"auto_sync_interval_minutes: {max(10, settings.auto_sync_interval_minutes)}",
                f"last_sync_at: {sync_state.last_sync_at or '(无)'}",
                f"last_sync_result: {sync_state.last_sync_result or '(无)'}",
                f"last_sync_source: {sync_state.last_sync_source or '(无)'}",
                f"next_sync_at: {sync_state.next_sync_at or '(无)'}",
                f"running: {'yes' if self.is_running else 'no'}",
                f"cached_students: {sync_state.filtered_count}",
            ]
        )
