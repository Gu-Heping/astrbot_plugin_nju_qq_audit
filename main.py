from __future__ import annotations

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from probe.event_store import ProbeEventStore, utc_now_iso
from probe.formatter import (
    format_event_summary,
    format_help,
    format_raw_event,
    format_recent,
    format_status,
)
from probe.sanitizer import (
    build_missing_raw_summary,
    classify_raw_message,
    parse_id_list,
    sanitize,
)

PLUGIN_NAME = "astrbot_plugin_nju_qq_audit"


@register(
    PLUGIN_NAME,
    "Gu-Heping",
    "南京大学新生 QQ 群入群审核插件，当前阶段为 OneBot request 探针",
    "v0.1.0",
)
class NjuQqAuditPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        )
        self.store = ProbeEventStore(
            self.data_dir,
            max_recent_events=int(self.config.get("max_recent_events", 20)),
        )

    async def initialize(self):
        self.store.update_max_recent(int(self.config.get("max_recent_events", 20)))
        logger.info(
            f"[{PLUGIN_NAME}] 探针已初始化，数据目录: {self.data_dir}"
        )

    def _admin_ids(self) -> set[str]:
        return parse_id_list(str(self.config.get("admin_qq_ids", "")))

    def _target_group_ids(self) -> set[str]:
        return parse_id_list(str(self.config.get("target_group_ids", "")))

    def _admin_configured(self) -> bool:
        return bool(self._admin_ids())

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        admin_ids = self._admin_ids()
        if not admin_ids:
            return False
        return event.get_sender_id() in admin_ids

    def _group_matches(self, group_id: str) -> bool:
        targets = self._target_group_ids()
        if not targets:
            return True
        return group_id in targets

    def _can_run_command(self, command: str, event: AstrMessageEvent) -> tuple[bool, str]:
        if event.get_message_type() != MessageType.FRIEND_MESSAGE:
            return False, "无权限"

        if self._is_admin(event):
            return True, ""

        if not self._admin_configured() and command in {"help", "status", "last"}:
            return True, ""

        if not self._admin_configured() and command in {"raw", "clear", "recent"}:
            return False, "未配置管理员（admin_qq_ids 为空），此命令不可用。"

        return False, "无权限"

    def _build_record(
        self,
        summary: dict,
        *,
        log_raw_event: bool,
        raw_message,
    ) -> dict:
        record = {
            "source": "astrbot_adapter",
            "received_at": utc_now_iso(),
            **summary,
        }
        if log_raw_event and raw_message is not None:
            plain = sanitize(raw_message)
            record["sanitized_raw"] = plain
        return record

    def _log_summary(self, record: dict) -> None:
        logger.info(
            "[audit_probe] "
            f"post_type={record.get('post_type') or '-'} "
            f"request_type={record.get('request_type') or '-'} "
            f"notice_type={record.get('notice_type') or '-'} "
            f"sub_type={record.get('sub_type') or '-'} "
            f"group_id={record.get('group_id') or '-'} "
            f"user_id={record.get('user_id') or '-'} "
            f"raw_present={record.get('raw_message_present')} "
            f"flag_present={record.get('flag_present')}"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_probe_event(self, event: AstrMessageEvent):
        if not self.config.get("probe_enabled", True):
            return

        msg = event.message_obj
        raw_message = getattr(msg, "raw_message", None)
        log_raw_event = bool(self.config.get("log_raw_event", False))

        if raw_message is not None:
            summary = classify_raw_message(raw_message)
            if summary and self._group_matches(summary.get("group_id", "")):
                record = self._build_record(
                    summary,
                    log_raw_event=log_raw_event,
                    raw_message=raw_message,
                )
                self._log_summary(record)
                await self.store.append(record)
            return

        group_id = event.get_group_id() or ""
        if not event.message_str and group_id:
            summary = build_missing_raw_summary(
                group_id=group_id,
                user_id=event.get_sender_id() or "",
                message_obj_type=type(msg).__name__,
            )
            if self._group_matches(group_id):
                record = self._build_record(
                    summary,
                    log_raw_event=False,
                    raw_message=None,
                )
                self._log_summary(record)
                await self.store.append(record)

    @filter.command_group("audit_probe")
    def audit_probe(self):
        pass

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("help")
    async def audit_probe_help(self, event: AstrMessageEvent):
        allowed, message = self._can_run_command("help", event)
        if not allowed:
            yield event.plain_result(message)
            return
        yield event.plain_result(format_help())

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("status")
    async def audit_probe_status(self, event: AstrMessageEvent):
        allowed, message = self._can_run_command("status", event)
        if not allowed:
            yield event.plain_result(message)
            return
        state = self.store.get_state()
        yield event.plain_result(
            format_status(
                probe_enabled=bool(self.config.get("probe_enabled", True)),
                recent_count=self.store.count(),
                last_request_group_at=state.get("last_request_group_at"),
                target_group_ids=str(self.config.get("target_group_ids", "")),
                data_dir=self.data_dir,
                log_raw_event=bool(self.config.get("log_raw_event", False)),
                admin_configured=self._admin_configured(),
            )
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("last")
    async def audit_probe_last(self, event: AstrMessageEvent):
        allowed, message = self._can_run_command("last", event)
        if not allowed:
            yield event.plain_result(message)
            return
        record = self.store.get_last()
        if record is None:
            yield event.plain_result("暂无最近事件记录。")
            return
        yield event.plain_result(format_event_summary(record))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("recent")
    async def audit_probe_recent(self, event: AstrMessageEvent):
        allowed, message = self._can_run_command("recent", event)
        if not allowed:
            yield event.plain_result(message)
            return
        yield event.plain_result(format_recent(self.store.get_recent(10)))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("raw")
    async def audit_probe_raw(self, event: AstrMessageEvent):
        allowed, message = self._can_run_command("raw", event)
        if not allowed:
            yield event.plain_result(message)
            return
        if not self.config.get("log_raw_event", False):
            yield event.plain_result(
                "未启用 raw 记录。请在插件配置中将 log_raw_event 设为 true 后重新触发事件。"
            )
            return
        yield event.plain_result(format_raw_event(self.store.get_last()))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit_probe.command("clear")
    async def audit_probe_clear(self, event: AstrMessageEvent, confirm: str = ""):
        allowed, message = self._can_run_command("clear", event)
        if not allowed:
            yield event.plain_result(message)
            return
        if confirm != "confirm":
            yield event.plain_result("请使用 /audit_probe clear confirm 确认清空。")
            return
        await self.store.clear()
        yield event.plain_result("已清空 recent events 与探针状态。")

    async def terminate(self):
        logger.info(f"[{PLUGIN_NAME}] 探针已卸载")
