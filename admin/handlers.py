from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp

from admin.notify import AdminNotifier
from config import PluginSettings, load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from onebot.actions import ActionClient, create_action_client, create_http_notify_client
from onebot.compat import invoke_probe_api
from onebot.platform_cache import cache_event_platform
from storage.admin_session_store import AdminSessionStore
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore


class PluginContext:
    def __init__(self, data_dir: Path, config, astrbot_context: Any) -> None:
        self.data_dir = data_dir
        self.config = config
        self.astrbot_context = astrbot_context
        self.settings = load_settings(config)
        self.cache = StudentCache(data_dir)
        self.requests = RequestsStore(data_dir / "requests.json")
        self.audit = AuditLog(data_dir / "audit.jsonl", self.settings)
        self.runtime = RuntimeStore(data_dir / "runtime.json")
        self.admin_sessions = AdminSessionStore(data_dir / "admin_sessions.json")
        self.actions: ActionClient = create_action_client(astrbot_context, self.settings)
        self._http_notify_client: ActionClient | None = None
        self._adapter_probe: dict[str, Any] = {}
        self.notifier = AdminNotifier(
            self.settings,
            self.actions,
            astrbot_context,
            self.admin_sessions,
            lambda: self._http_notify_client,
        )
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
        self._platform_id: str | None = None
        self._event_bot: Any | None = None

    def reload_settings(self) -> None:
        self.settings = load_settings(self.config)
        self.audit.settings = self.settings
        old_platform_id = self._platform_id
        old_event_bot = self._event_bot
        self.actions = create_action_client(self.astrbot_context, self.settings)
        from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient

        if isinstance(self.actions, AstrBotAdapterActionClient):
            self.actions.restore_hints(platform_id=old_platform_id, event_bot=old_event_bot)
        self._http_notify_client = create_http_notify_client(self.settings)
        self.notifier.reload_settings(
            self.settings,
            self.actions,
            self.astrbot_context,
            self.admin_sessions,
            lambda: self._http_notify_client,
        )
        self.pipeline.reload_settings(self.settings, self.actions, self.notifier)
        self._adapter_probe = {}

    async def start(self) -> None:
        await self.actions.start()
        self._http_notify_client = create_http_notify_client(self.settings)
        if self._http_notify_client is not None:
            await self._http_notify_client.start()
        self._http_session = aiohttp.ClientSession()
        await self._probe_adapter()

    async def stop(self) -> None:
        await self.actions.close()
        if self._http_notify_client is not None:
            await self._http_notify_client.close()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    async def _probe_adapter(self) -> None:
        from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient

        if isinstance(self.actions, AstrBotAdapterActionClient):
            self._adapter_probe = await invoke_probe_api(self.actions)
        else:
            self._adapter_probe = {"adapter_action_available": "n/a"}

    async def get_adapter_probe(self) -> dict[str, Any]:
        if not self._adapter_probe:
            await self._probe_adapter()
        return self._adapter_probe

    async def record_admin_session(self, admin_qq: str, umo: str) -> None:
        if not admin_qq or not umo:
            return
        if self.settings.admin_qq_ids and admin_qq not in self.settings.admin_qq_ids:
            return
        await self.admin_sessions.record(admin_qq, umo)

    def remember_event_platform(self, event: Any) -> None:
        cache_event_platform(self, event)

    def effective_mode(self) -> tuple[str, str]:
        from config import get_effective_mode

        return get_effective_mode(self.settings, self.runtime.get_mode_override())

    async def run_sync(self) -> str:
        from data_source.njutable_provider import sync_students

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
