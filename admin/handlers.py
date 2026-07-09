from __future__ import annotations

from pathlib import Path

import aiohttp

from admin.formatter import (
    format_help,
    format_pending_list,
    format_probe_status,
    format_request_detail,
    format_stats,
    format_status,
)
from admin.notify import AdminNotifier
from admin.permissions import can_run_command
from config import PluginSettings, get_effective_mode, load_settings
from core.pipeline import AuditPipeline
from data_source.njutable_provider import load_students_for_audit, sync_students
from data_source.student_cache import StudentCache
from onebot.http_actions import OneBotHttpActions
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore


class PluginContext:
    def __init__(self, data_dir: Path, config) -> None:
        self.data_dir = data_dir
        self.config = config
        self.settings = load_settings(config)
        self.cache = StudentCache(data_dir)
        self.requests = RequestsStore(data_dir / "requests.json")
        self.audit = AuditLog(data_dir / "audit.jsonl", self.settings)
        self.runtime = RuntimeStore(data_dir / "runtime.json")
        self.actions = OneBotHttpActions(self.settings)
        self.notifier = AdminNotifier(self.settings, self.actions)
        self.pipeline = AuditPipeline(
            self.settings,
            self.requests,
            self.audit,
            self.runtime,
            self.cache,
            self.actions,
            self.notifier,
        )
        self._http_session: aiohttp.ClientSession | None = None

    def reload_settings(self) -> None:
        self.settings = load_settings(self.config)
        self.audit.settings = self.settings
        self.actions.settings = self.settings
        self.notifier.reload_settings(self.settings)
        self.pipeline.reload_settings(self.settings)

    async def start(self) -> None:
        await self.actions.start()
        self._http_session = aiohttp.ClientSession()

    async def stop(self) -> None:
        await self.actions.close()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    def effective_mode(self) -> tuple[str, str]:
        return get_effective_mode(self.settings, self.runtime.get_mode_override())

    async def run_sync(self) -> str:
        session = self._http_session or aiohttp.ClientSession()
        own = self._http_session is None
        try:
            state = await sync_students(self.settings, self.cache, session)
            return (
                f"同步成功: source={state.source}, rows={state.row_count}, "
                f"filtered={state.filtered_count}"
            )
        except Exception as exc:
            cached = self.cache.load_students()
            return (
                f"同步失败: {type(exc).__name__}。已保留旧缓存 {len(cached)} 条。"
            )
        finally:
            if own:
                await session.close()
