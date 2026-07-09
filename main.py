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

from admin.command_resolver import (
    map_action_error,
    parse_no_command_reason,
    resolve_request_ref,
)
from admin.formatter import (
    format_help,
    format_probe_api,
    format_probe_status,
    format_stats,
)
from admin.ux_formatter import (
    format_auto_warning,
    format_debug,
    format_home,
    format_list,
    format_mode_changed,
    format_no_result,
    format_off_warning,
    format_ok_result,
    format_view,
)
from admin.handlers import PluginContext
from admin.ctx_compat import ensure_ctx_compat
from admin.pending import fetch_pending_for_admin
from admin.release import (
    format_release_help,
    format_release_preview,
    format_release_result,
    list_releasable,
)
from admin.report import build_report_data, format_report, format_unknown
from admin.permissions import can_run_command
from data_source.njutable_provider import load_students_for_audit
from onebot.event_extract import extract_group_request, extract_raw_dict, is_notice_event
from onebot.compat import invoke_probe_api
from onebot.platform_cache import cache_event_platform
from probe.event_store import ProbeEventStore, utc_now_iso
from probe.formatter import format_event_summary, format_raw_event, format_recent as format_probe_recent
from probe.sanitizer import build_missing_raw_summary, classify_raw_message, sanitize

PLUGIN_NAME = "astrbot_plugin_nju_qq_audit"
PLUGIN_VERSION = "v0.3.3"


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
        self.ctx = PluginContext(self.data_dir, self.config, self.context)
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

    def _remember_event_platform(self, event: AstrMessageEvent) -> None:
        cache_event_platform(self.ctx, event)

    async def _ensure_ctx_compat(self) -> None:
        ensure_ctx_compat(self.ctx)

    async def _record_admin_session(self, event: AstrMessageEvent) -> None:
        await self._ensure_ctx_compat()
        self._remember_event_platform(event)
        umo = getattr(event, "unified_msg_origin", None)
        if umo:
            await self.ctx.record_admin_session(event.get_sender_id(), umo)

    async def _render_home(self, event: AstrMessageEvent) -> str:
        mode, _ = self.ctx.effective_mode()
        students = load_students_for_audit(self._settings(), self.ctx.cache)
        pending = await self.ctx.requests.list_pending(limit=1000)
        sync_state = self.ctx.cache.load_sync_state()
        adapter_probe = await self.ctx.get_adapter_probe()
        releasable = await list_releasable(self.ctx.requests, self._settings())
        return format_home(
            self._settings(),
            effective_mode=mode,
            student_count=len(students),
            pending_count=len(pending),
            sync_state=sync_state,
            adapter_probe=adapter_probe,
            releasable_count=len(releasable),
            release_running=self.ctx.release_service.is_running,
        )

    async def _run_release_batch(self, event: AstrMessageEvent, count: int | None) -> str:
        result = await self.ctx.release_service.run_batch(
            requests_store=self.ctx.requests,
            pipeline=self.ctx.pipeline,
            settings=self._settings(),
            admin_user_id=event.get_sender_id(),
            count=count,
            audit_log=self.ctx.audit,
        )
        if result is None:
            return "已有分批任务进行中，请稍后再试。"
        return format_release_result(result, self._settings())

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
        self._remember_event_platform(event)
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
    async def audit_root(self, event: AstrMessageEvent):
        if (event.message_str or "").strip() != "/audit":
            return
        allowed, message = can_run_command(self._settings(), "home", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        yield event.plain_result(await self._render_home(event))

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
        yield event.plain_result(await self._render_home(event))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("debug")
    async def audit_debug(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "debug", event)
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
            format_debug(
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
    @audit.command("list")
    async def audit_list(self, event: AstrMessageEvent, limit: int = 10):
        allowed, message = can_run_command(self._settings(), "list", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        items, index_map = await fetch_pending_for_admin(self.ctx, event.get_sender_id(), limit)
        yield event.plain_result(format_list(items, index_map))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("view")
    async def audit_view(self, event: AstrMessageEvent, ref: str):
        allowed, message = can_run_command(self._settings(), "view", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            ref,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        yield event.plain_result(format_view(resolved.request, resolved.index))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("ok")
    async def audit_ok(self, event: AstrMessageEvent, ref: str):
        allowed, message = can_run_command(self._settings(), "ok", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            ref,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        result = await self.ctx.pipeline.admin_approve(resolved.request, event.get_sender_id())
        if result.ok:
            yield event.plain_result(format_ok_result(resolved.request, resolved.index))
        else:
            yield event.plain_result(map_action_error(result.message))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("no")
    async def audit_no(self, event: AstrMessageEvent, ref: str, reason: str = ""):
        allowed, message = can_run_command(self._settings(), "no", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        reject_reason = reason.strip() or parse_no_command_reason(event.message_str or "", ref)
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            ref,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        result = await self.ctx.pipeline.admin_reject(
            resolved.request, event.get_sender_id(), reject_reason
        )
        if result.ok:
            yield event.plain_result(format_no_result(resolved.request, resolved.index, reject_reason))
        else:
            yield event.plain_result(map_action_error(result.message))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("auto")
    async def audit_auto(self, event: AstrMessageEvent, confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "auto", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result(format_auto_warning())
            return
        await self.ctx.runtime.set_mode("auto", event.get_sender_id())
        yield event.plain_result(format_mode_changed("auto"))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("manual")
    async def audit_manual(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "manual", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        await self.ctx.runtime.set_mode("manual", event.get_sender_id())
        yield event.plain_result(format_mode_changed("manual"))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("record")
    async def audit_record(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "record", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        await self.ctx.runtime.set_mode("record-only", event.get_sender_id())
        yield event.plain_result(format_mode_changed("record-only"))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("off")
    async def audit_off(self, event: AstrMessageEvent, confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "off", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result(format_off_warning())
            return
        await self.ctx.runtime.set_mode("off", event.get_sender_id())
        yield event.plain_result(format_mode_changed("off"))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("reset-mode")
    async def audit_reset_mode(self, event: AstrMessageEvent, confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "reset-mode", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result("请使用 /audit reset-mode confirm")
            return
        await self.ctx.runtime.clear_mode()
        mode, source = self.ctx.effective_mode()
        yield event.plain_result(f"已恢复插件配置 mode: {mode} ({source})")

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
    async def audit_sync(self, event: AstrMessageEvent, action: str = ""):
        allowed, message = can_run_command(self._settings(), "sync", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if action == "status":
            sync_state = self.ctx.cache.load_sync_state()
            yield event.plain_result(
                self.ctx.sync_scheduler.format_status(self._settings(), sync_state)
            )
            return
        result = await self.ctx.run_sync(source="manual")
        yield event.plain_result(result)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("release")
    async def audit_release(
        self, event: AstrMessageEvent, arg1: str = "", arg2: str = "", arg3: str = ""
    ):
        allowed, message = can_run_command(self._settings(), "release", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        settings = self._settings()
        releasable = await list_releasable(self.ctx.requests, settings)
        if not arg1:
            yield event.plain_result(format_release_help(len(releasable), settings))
            return
        if arg1 == "preview":
            preview = await self.ctx.release_service.preview(self.ctx.requests, settings)
            yield event.plain_result(format_release_preview(preview, settings))
            return
        if arg2 != "confirm":
            yield event.plain_result("请使用 /audit release <数量|all> confirm")
            return
        if arg1 == "all":
            count = None
        else:
            try:
                count = max(1, int(arg1))
            except ValueError:
                yield event.plain_result("数量无效，请使用数字或 all")
                return
        yield event.plain_result(await self._run_release_batch(event, count))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("batch")
    async def audit_batch(
        self, event: AstrMessageEvent, kind: str = "", count: str = "", confirm: str = ""
    ):
        allowed, message = can_run_command(self._settings(), "batch", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if kind != "strong" or confirm != "confirm":
            yield event.plain_result("请使用 /audit batch strong 10 confirm")
            return
        try:
            batch_count = max(1, int(count))
        except ValueError:
            yield event.plain_result("请使用 /audit batch strong 10 confirm")
            return
        yield event.plain_result(await self._run_release_batch(event, batch_count))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("temp")
    async def audit_temp(self, event: AstrMessageEvent, count: str = "", confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "temp", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result("请使用 /audit temp 10 confirm")
            return
        try:
            batch_count = max(1, int(count))
        except ValueError:
            yield event.plain_result("请使用 /audit temp 10 confirm")
            return
        yield event.plain_result(await self._run_release_batch(event, batch_count))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("unknown")
    async def audit_unknown(self, event: AstrMessageEvent, limit: int = 5):
        allowed, message = can_run_command(self._settings(), "unknown", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        data = await build_report_data(self.ctx.requests, self._settings(), days=7, sample_limit=limit)
        yield event.plain_result(format_unknown(data, sample_limit=limit))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("report")
    async def audit_report(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "report", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        data = await build_report_data(self.ctx.requests, self._settings(), days=7)
        sync_state = self.ctx.cache.load_sync_state()
        yield event.plain_result(
            format_report(
                data,
                sync_state,
                release_running=self.ctx.release_service.is_running,
            )
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("pending")
    async def audit_pending(self, event: AstrMessageEvent, limit: int = 10):
        allowed, message = can_run_command(self._settings(), "pending", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        limit = max(1, min(int(limit), 50))
        items, index_map = await fetch_pending_for_admin(self.ctx, event.get_sender_id(), limit)
        yield event.plain_result(format_list(items, index_map))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("request")
    async def audit_request(self, event: AstrMessageEvent, req_id: str):
        allowed, message = can_run_command(self._settings(), "request", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            req_id,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        yield event.plain_result(format_view(resolved.request, resolved.index))

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
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            req_id,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        result = await self.ctx.pipeline.admin_approve(resolved.request, event.get_sender_id())
        if result.ok:
            yield event.plain_result(format_ok_result(resolved.request, resolved.index))
        else:
            yield event.plain_result(map_action_error(result.message))

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
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            req_id,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        result = await self.ctx.pipeline.admin_reject(
            resolved.request, event.get_sender_id(), "管理员人工拒绝"
        )
        if result.ok:
            yield event.plain_result(format_no_result(resolved.request, resolved.index, "管理员人工拒绝"))
        else:
            yield event.plain_result(map_action_error(result.message))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("process")
    async def audit_process(self, event: AstrMessageEvent, kind: str = "", confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "process", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if kind != "strong" or confirm != "confirm":
            yield event.plain_result(
                "请使用 /audit process strong confirm（建议使用 /audit release 10 confirm）"
            )
            return
        yield event.plain_result(await self._run_release_batch(event, None))

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
        cache_event_platform(self.ctx, event)
        from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient

        if isinstance(self.ctx.actions, AstrBotAdapterActionClient):
            probe = await invoke_probe_api(self.ctx.actions, event)
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
