from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from admin.formatter import (
    format_help,
    format_pending_list,
    format_probe_api,
    format_probe_status,
    format_request_detail,
    format_stats,
    format_status,
)
from admin.handlers import PluginContext
from admin.permissions import can_run_command
from data_source.njutable_provider import load_students_for_audit
from onebot.event_extract import extract_group_request, extract_raw_dict, is_notice_event
from probe.event_store import ProbeEventStore, utc_now_iso
from probe.formatter import format_event_summary, format_raw_event, format_recent as format_probe_recent
from probe.sanitizer import build_missing_raw_summary, classify_raw_message, sanitize

PLUGIN_NAME = "astrbot_plugin_nju_qq_audit"
PLUGIN_VERSION = "v0.2.2"


@register(
    PLUGIN_NAME,
    "Gu-Heping",
    "南京大学新生 QQ 群入群审核插件",
    PLUGIN_VERSION,
)
class NjuQqAuditPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.ctx = PluginContext(self.data_dir, config, context)
        self.probe_store = ProbeEventStore(
            self.data_dir,
            max_recent_events=int(config.get("max_recent_events", 20)),
        )

    async def initialize(self):
        self.ctx.reload_settings()
        self.probe_store.update_max_recent(int(self.config.get("max_recent_events", 20)))
        await self.ctx.start()
        if self.ctx.settings.student_source == "mock" and not self.ctx.cache.load_students():
            from data_source.mock_provider import generate_mock_students

            self.ctx.cache.save_students(generate_mock_students())
        logger.info("[%s] 插件已初始化 %s, data_dir=%s", PLUGIN_NAME, PLUGIN_VERSION, self.data_dir)

    async def terminate(self):
        await self.ctx.stop()
        logger.info("[%s] 插件已卸载", PLUGIN_NAME)

    def _settings(self):
        return self.ctx.settings

    async def _record_admin_session(self, event: AstrMessageEvent) -> None:
        self.ctx.remember_event_platform(event)
        umo = getattr(event, "unified_msg_origin", None)
        if umo:
            await self.ctx.record_admin_session(event.get_sender_id(), umo)

    def _probe_group_matches(self, group_id: str) -> bool:
        targets = self._settings().target_group_ids
        if not targets:
            return True
        return group_id in targets

    async def _handle_probe(self, event: AstrMessageEvent, raw_message) -> None:
        if not self._settings().probe_enabled:
            return
        log_raw = self._settings().log_raw_event
        if raw_message is not None:
            summary = classify_raw_message(raw_message)
            if summary and self._probe_group_matches(summary.get("group_id", "")):
                record = {
                    "source": "astrbot_adapter",
                    "received_at": utc_now_iso(),
                    **summary,
                }
                if log_raw:
                    record["sanitized_raw"] = sanitize(raw_message)
                await self.probe_store.append(record)
            return
        group_id = event.get_group_id() or ""
        if not event.message_str and group_id and self._probe_group_matches(group_id):
            summary = build_missing_raw_summary(
                group_id=group_id,
                user_id=event.get_sender_id() or "",
                message_obj_type=type(event.message_obj).__name__,
            )
            await self.probe_store.append(
                {"source": "astrbot_adapter", "received_at": utc_now_iso(), **summary}
            )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_events(self, event: AstrMessageEvent):
        self.ctx.remember_event_platform(event)
        raw = extract_raw_dict(event.message_obj)
        await self._handle_probe(event, raw)
        if raw and is_notice_event(raw):
            return
        join_req = extract_group_request(raw)
        if join_req:
            await self.ctx.pipeline.handle_group_request(join_req)

    @filter.command_group("audit")
    def audit(self):
        pass

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("help")
    async def audit_help(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "help", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        yield event.plain_result(format_help())

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("status")
    async def audit_status(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "status", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        mode, source = self.ctx.effective_mode()
        students = load_students_for_audit(self._settings(), self.ctx.cache)
        pending = await self.ctx.requests.list_pending(limit=1000)
        sync_state = self.ctx.cache.load_sync_state()
        adapter_probe = await self.ctx.get_adapter_probe()
        admin_session_stats = self.ctx.admin_sessions.stats(self._settings().admin_qq_ids)
        yield event.plain_result(
            format_status(
                self._settings(),
                effective_mode=mode,
                mode_source=source,
                student_count=len(students),
                pending_count=len(pending),
                sync_state=sync_state,
                probe_count=self.probe_store.count(),
                data_dir=str(self.data_dir),
                adapter_probe=adapter_probe,
                admin_session_stats=admin_session_stats,
            )
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("mode")
    async def audit_mode(self, event: AstrMessageEvent, arg: str = "", confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "mode", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if not arg:
            mode, source = self.ctx.effective_mode()
            yield event.plain_result(f"effective_mode: {mode}\nmode_source: {source}")
            return
        arg = arg.strip().lower()
        if arg == "reset":
            if confirm != "confirm":
                yield event.plain_result("请使用 /audit mode reset confirm")
                return
            await self.ctx.runtime.clear_mode()
            mode, source = self.ctx.effective_mode()
            yield event.plain_result(f"已恢复插件配置 mode: {mode} ({source})")
            return
        if arg in {"auto", "off"} and confirm != "confirm":
            yield event.plain_result(f"请使用 /audit mode {arg} confirm")
            return
        if arg not in {"record-only", "manual", "auto", "off"}:
            yield event.plain_result("无效 mode，可选: record-only/manual/auto/off")
            return
        await self.ctx.runtime.set_mode(arg, event.get_sender_id())
        yield event.plain_result(f"runtime mode 已切换为: {arg}")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("sync")
    async def audit_sync(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "sync", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        result = await self.ctx.run_sync()
        yield event.plain_result(result)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("pending")
    async def audit_pending(self, event: AstrMessageEvent, limit: int = 10):
        allowed, message = can_run_command(self._settings(), "pending", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        limit = max(1, min(int(limit), 50))
        items = await self.ctx.requests.list_pending(limit=limit)
        yield event.plain_result(format_pending_list(items))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("request")
    async def audit_request(self, event: AstrMessageEvent, req_id: str):
        allowed, message = can_run_command(self._settings(), "request", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        req = await self.ctx.requests.resolve_by_id_or_prefix(req_id)
        if not req:
            yield event.plain_result(f"未找到请求: {req_id}")
            return
        yield event.plain_result(format_request_detail(req))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("approve")
    async def audit_approve(self, event: AstrMessageEvent, req_id: str, confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "approve", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result("请使用 /audit approve <id> confirm")
            return
        req = await self.ctx.requests.resolve_by_id_or_prefix(req_id)
        if not req:
            yield event.plain_result(f"未找到请求: {req_id}")
            return
        result = await self.ctx.pipeline.admin_approve(req, event.get_sender_id())
        yield event.plain_result("已通过" if result.ok else f"操作失败: {result.message}")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("reject")
    async def audit_reject(self, event: AstrMessageEvent, req_id: str, confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "reject", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result("请使用 /audit reject <id> confirm")
            return
        req = await self.ctx.requests.resolve_by_id_or_prefix(req_id)
        if not req:
            yield event.plain_result(f"未找到请求: {req_id}")
            return
        result = await self.ctx.pipeline.admin_reject(req, event.get_sender_id())
        yield event.plain_result("已拒绝" if result.ok else f"操作失败: {result.message}")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("process")
    async def audit_process(self, event: AstrMessageEvent, kind: str = "", confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "process", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if kind != "strong" or confirm != "confirm":
            yield event.plain_result("请使用 /audit process strong confirm")
            return
        results = await self.ctx.pipeline.process_strong_pending(event.get_sender_id())
        yield event.plain_result("\n".join(results) if results else "没有可处理的 strong pending 请求。")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("stats")
    async def audit_stats(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "stats", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        stats = await self.ctx.requests.get_stats()
        yield event.plain_result(format_stats(stats))

    @audit.group("probe")
    def audit_probe(self):
        pass

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("status")
    async def audit_probe_status(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "probe_status", event)
        if not allowed:
            yield event.plain_result(message)
            return
        state = self.probe_store.get_state()
        yield event.plain_result(
            format_probe_status(
                self._settings(),
                self.probe_store.count(),
                str(self.data_dir),
                state.get("last_request_group_at"),
            )
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("last")
    async def audit_probe_last(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "probe_last", event)
        if not allowed:
            yield event.plain_result(message)
            return
        record = self.probe_store.get_last()
        if not record:
            yield event.plain_result("暂无最近事件记录。")
            return
        yield event.plain_result(format_event_summary(record))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("recent")
    async def audit_probe_recent(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "probe", event)
        if not allowed:
            yield event.plain_result(message)
            return
        yield event.plain_result(format_probe_recent(self.probe_store.get_recent(10)))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("api")
    async def audit_probe_api(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "probe_api", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient

        if isinstance(self.ctx.actions, AstrBotAdapterActionClient):
            probe = await self.ctx.actions.probe_api(event)
        else:
            probe = {
                "adapter_found": "n/a",
                "adapter_action_available": "n/a",
                "test_action": "",
                "result": "skipped",
                "message": f"action_backend={self._settings().onebot_action_backend}",
            }
        yield event.plain_result(format_probe_api(probe))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("raw")
    async def audit_probe_raw(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "raw", event)
        if not allowed:
            yield event.plain_result(message)
            return
        if not self._settings().log_raw_event:
            yield event.plain_result("未启用 raw 记录。请在插件配置中将 log_raw_event 设为 true。")
            return
        yield event.plain_result(format_raw_event(self.probe_store.get_last()))

    @filter.command_group("audit_probe")
    def audit_probe_legacy(self):
        pass

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe_legacy.command("status")
    async def legacy_probe_status(self, event: AstrMessageEvent):
        async for result in self.audit_probe_status(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe_legacy.command("last")
    async def legacy_probe_last(self, event: AstrMessageEvent):
        async for result in self.audit_probe_last(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe_legacy.command("recent")
    async def legacy_probe_recent(self, event: AstrMessageEvent):
        async for result in self.audit_probe_recent(event):
            yield result
