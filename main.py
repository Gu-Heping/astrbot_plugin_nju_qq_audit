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

from admin.action_error import format_action_outcome_message
from admin.command_resolver import (
    parse_dismiss_command,
    parse_no_command_reason,
    resolve_request_ref,
    sanitize_action_message,
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
from admin.receipts import (
    format_already_terminal_result,
    format_dismiss_result,
    format_mark_external_result,
    format_restore_result,
    resolve_display_labels,
    resolve_one_item_labels,
)
from admin.handlers import PluginContext
from admin.ctx_compat import ensure_ctx_compat
from admin.labels import applicant_summary
from admin.pending import fetch_pending_for_admin
from admin.lookup import (
    format_lookup_help,
    format_lookup_result,
    parse_lookup_args,
    run_lookup,
)
from admin.release import (
    format_catchup_help,
    format_catchup_preview,
    format_catchup_result,
    format_release_help,
    format_release_preview,
    format_release_result,
    list_releasable,
)
from admin.sweep import (
    collect_sweep_preview,
    format_sweep_help,
    format_sweep_preview,
    format_sweep_result,
    parse_sweep_command,
    run_sweep,
)
from admin.report import build_report_data, format_report, format_unknown
from admin.permissions import can_run_command
from data_source.njutable_provider import load_students_for_audit
from onebot.event_extract import (
    extract_group_decrease,
    extract_group_increase,
    extract_group_request,
    extract_raw_dict,
    is_notice_event,
)
from onebot.compat import invoke_probe_api
from onebot.platform_cache import cache_event_platform
from probe.event_store import ProbeEventStore, utc_now_iso
from probe.formatter import format_event_summary, format_raw_event, format_recent as format_probe_recent
from probe.sanitizer import build_missing_raw_summary, classify_raw_message, sanitize

from core.version import (
    DUPLICATE_POLICY_VERSION,
    PENDING_UPDATE_POLICY_VERSION,
    PLUGIN_VERSION,
    RECONCILE_LOGIC_VERSION,
    get_git_commit,
)
from profiles.router import overlapping_group_ids

PLUGIN_NAME = "astrbot_plugin_nju_qq_audit"


def _format_stale_list(items: list, index_map: dict[int, str]) -> str:
    if not items:
        return "目前没有 stale 申请。"
    lines = [f"stale 申请：{len(items)} 条", ""]
    for idx, item in enumerate(items, start=1):
        public = item.to_public_dict()
        summary = applicant_summary(item)
        comment = (public.get("comment") or "")[:80]
        last_action = public.get("last_action_result") or {}
        reason = sanitize_action_message(last_action.get("message"))
        lines.extend(
            [
                f"[{idx}] {summary}",
                f"群：{public.get('group_id', '')}",
                f"验证：{comment or '（空）'}",
                f"原因：{reason}",
            ]
        )
        lines.append(f"/audit view {idx}  |  /audit restore {idx} confirm")
        lines.append(f"/audit mark-external {idx} confirm")
        lines.append("")
    lines.append("编号来自本次 /audit stale 列表，30 分钟内有效。")
    return "\n".join(lines)


async def _fetch_stale_for_admin(
    ctx, admin_id: str, limit: int = 10
) -> tuple[list, dict[int, str]]:
    try:
        from admin.pending import fetch_stale_for_admin as _impl

        return await _impl(ctx, admin_id, limit)
    except ImportError:
        ensure_ctx_compat(ctx)
        limit = max(1, min(int(limit), 50))
        items = await ctx.requests.list_stale(limit=limit)
        cache_key = f"{admin_id}:stale"
        index_map = await ctx.list_cache.refresh(cache_key, [item.id for item in items])
        return items, index_map


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
        logger.info(
            "[%s] 插件已初始化 %s reconcile=%s duplicate=%s git=%s data_dir=%s",
            PLUGIN_NAME,
            PLUGIN_VERSION,
            RECONCILE_LOGIC_VERSION,
            DUPLICATE_POLICY_VERSION,
            get_git_commit() or "n/a",
            self.data_dir,
        )
        for warning in self.ctx.config_warnings():
            logger.warning("[%s] config: %s", PLUGIN_NAME, warning)

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
        grad_enabled = bool(self._settings().grad_enabled)
        grad_students = self.ctx.grad_cache.load_students() if grad_enabled else []
        grad_sync_state = self.ctx.grad_cache.load_sync_state()
        grad_pending = sum(
            1
            for r in pending
            if (getattr(r, "profile", None) or "undergraduate") == "graduate"
        )
        under_pending = len(pending) - grad_pending
        adapter_probe = await self.ctx.get_adapter_probe()
        releasable = await list_releasable(self.ctx.requests, self._settings())
        return format_home(
            self._settings(),
            effective_mode=mode,
            student_count=len(students),
            pending_count=under_pending,
            sync_state=sync_state,
            grad_enabled=grad_enabled,
            grad_target_group_ids=sorted(self._settings().grad_target_group_ids),
            grad_student_count=len(grad_students),
            grad_pending_count=grad_pending,
            grad_sync_state=grad_sync_state,
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
        settings = self._settings()
        under = settings.target_group_ids
        grad = settings.grad_target_group_ids if settings.grad_enabled else frozenset()
        if not under and not grad:
            return True
        return group_id in under or group_id in grad

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

    async def _format_action_failure(self, request_id: str, result) -> str:
        updated = await self.ctx.requests.get_by_id(request_id)
        final_status = updated.status if updated else "pending"
        return format_action_outcome_message(
            result.message,
            result.retcode,
            final_status=final_status,
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_events(self, event: AstrMessageEvent):
        self._remember_event_platform(event)
        if event.get_message_type() == MessageType.FRIEND_MESSAGE:
            sender = event.get_sender_id() or ""
            if sender in self._settings().admin_qq_ids:
                umo = getattr(event, "unified_msg_origin", None)
                if umo:
                    await self.ctx.record_admin_session(sender, umo)
        raw = extract_raw_dict(event.message_obj)
        await self._handle_probe(event, raw)
        if raw and is_notice_event(raw):
            decrease = extract_group_decrease(raw)
            if decrease:
                logger.info(
                    "[audit] notice.group_decrease group_id=%s user_id=%s sub_type=%s operator_id=%s",
                    decrease.group_id,
                    decrease.user_id,
                    decrease.sub_type,
                    decrease.operator_id,
                )
                try:
                    await self.ctx.pipeline.handle_group_decrease(decrease)
                except Exception:
                    logger.exception("[audit] handle group_decrease failed")
                return
            increase = extract_group_increase(raw)
            if increase:
                logger.info(
                    "[audit] notice.group_increase group_id=%s user_id=%s sub_type=%s operator_id=%s",
                    increase.group_id,
                    increase.user_id,
                    increase.sub_type,
                    increase.operator_id,
                )
                try:
                    await self.ctx.pipeline.handle_group_increase(increase)
                except Exception:
                    logger.exception("[audit] handle group_increase failed")
                try:
                    reconcile = await self.ctx.pipeline.reconcile_external_join(
                        increase.group_id,
                        increase.user_id,
                        notice_sub_type=increase.sub_type,
                        operator_id=increase.operator_id,
                        self_id=increase.self_id,
                        list_cache=self.ctx.list_cache,
                    )
                    logger.info(
                        "[audit] reconcile_external_join handled=%s reason=%s request_id=%s",
                        reconcile.handled,
                        reconcile.reason,
                        reconcile.request_id,
                    )
                    if not reconcile.handled:
                        logger.debug(
                            "[audit] reconcile not handled: reason=%s message=%s logic=%s",
                            reconcile.reason,
                            reconcile.message,
                            RECONCILE_LOGIC_VERSION,
                        )
                except Exception:
                    logger.exception("[audit] reconcile external join failed")
            return
        join_req = extract_group_request(raw)
        if join_req:
            umo = getattr(event, "unified_msg_origin", None)
            if umo:
                join_req.umo = str(umo)
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
    async def audit_help(self, event: AstrMessageEvent, topic: str = ""):
        allowed, message = can_run_command(self._settings(), "help", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        yield event.plain_result(await self._render_help(topic=topic))

    async def _render_help(self, topic: str = "") -> str:
        ensure_ctx_compat(self.ctx)
        mode, _ = self.ctx.effective_mode()
        pending = await self.ctx.requests.list_pending(limit=1000)
        releasable_count = 0
        try:
            releasable = await list_releasable(self.ctx.requests, self._settings())
            releasable_count = len(releasable)
        except Exception:
            logger.debug("[audit] help: releasable count unavailable", exc_info=True)
        try:
            return format_help(
                effective_mode=mode,
                pending_count=len(pending),
                releasable_count=releasable_count,
                topic=topic,
            )
        except TypeError:
            # 热重载后 main.py / formatter.py 版本不一致时的兼容
            return format_help()

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
        group_system_msg_probe = {}
        try:
            from onebot.group_system_msg import describe_group_system_msg_result

            target_groups = sorted(self._settings().target_group_ids)
            probe_group = target_groups[0] if target_groups else None
            gsm = await self.ctx.actions.get_group_system_msg(probe_group)
            group_system_msg_probe = describe_group_system_msg_result(gsm)
        except Exception:
            group_system_msg_probe = {
                "action_status": "failed",
                "parser_variant": "unavailable",
                "data_type": "unavailable",
            }
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
                plugin_version=PLUGIN_VERSION,
                reconcile_logic_version=RECONCILE_LOGIC_VERSION,
                duplicate_policy_version=DUPLICATE_POLICY_VERSION,
                pending_update_policy_version=PENDING_UPDATE_POLICY_VERSION,
                git_commit=get_git_commit(),
                group_system_msg_probe=group_system_msg_probe,
                grad_cache_count=len(self.ctx.grad_cache.load_students()),
                grad_sync_state=self.ctx.grad_cache.load_sync_state(),
                group_overlap_warning=(
                    ",".join(sorted(overlapping_group_ids(self._settings()))) or None
                ),
                config_warnings=self.ctx.config_warnings(),
            )
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("list")
    async def audit_list(
        self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""
    ):
        allowed, message = can_run_command(self._settings(), "list", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        profile = None
        limit = 10
        token = (arg1 or "").strip().lower()
        if token in {"grad", "graduate", "研究生"}:
            profile = "graduate"
            if arg2.isdigit():
                limit = int(arg2)
        elif token in {"undergrad", "undergraduate", "本科"}:
            profile = "undergraduate"
            if arg2.isdigit():
                limit = int(arg2)
        elif token.isdigit():
            limit = int(token)
        reconcile_summary = await self.ctx.pipeline.reconcile_active_pending(
            source="audit_list",
            list_cache=self.ctx.list_cache,
            profiles=frozenset({profile}) if profile else None,
        )
        items, index_map = await fetch_pending_for_admin(
            self.ctx, event.get_sender_id(), limit, profile=profile
        )
        group_labels, user_labels = await resolve_display_labels(
            getattr(self.ctx, "display", None), items
        )
        yield event.plain_result(
            format_list(
                items,
                index_map,
                reconcile_summary=reconcile_summary,
                group_labels=group_labels,
                user_labels=user_labels,
                list_profile=profile,
            )
        )

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
            for_view=True,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        group_label, user_label = await resolve_one_item_labels(
            getattr(self.ctx, "display", None), resolved.request
        )
        yield event.plain_result(
            format_view(
                resolved.request,
                resolved.index,
                group_label=group_label,
                user_label=user_label,
            )
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("lookup")
    async def audit_lookup(self, event: AstrMessageEvent, arg1: str = "", arg2: str = "", arg3: str = ""):
        allowed, message = can_run_command(self._settings(), "lookup", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        raw = (event.message_str or "").strip()
        payload = raw
        for prefix in ("/audit lookup", "audit lookup"):
            if payload.lower().startswith(prefix):
                payload = payload[len(prefix) :].strip()
                break
        else:
            # Framework may only pass split args when message_str unavailable
            payload = " ".join(p for p in (arg1, arg2, arg3) if p).strip()
        if not payload:
            yield event.plain_result(format_lookup_help())
            return
        name, student_id, major = parse_lookup_args(payload)
        if not name and not student_id and not major:
            yield event.plain_result(format_lookup_help())
            return
        result = run_lookup(
            self._settings(),
            self.ctx.cache,
            name=name,
            student_id=student_id,
            major=major,
        )
        yield event.plain_result(format_lookup_result(result))

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
        result = await self.ctx.pipeline.admin_approve(
            resolved.request,
            event.get_sender_id(),
            list_cache=self.ctx.list_cache,
        )
        if result.ok:
            group_label, user_label = await resolve_one_item_labels(
                getattr(self.ctx, "display", None), resolved.request
            )
            yield event.plain_result(
                format_ok_result(
                    resolved.request,
                    resolved.index,
                    group_label=group_label,
                    user_label=user_label,
                )
            )
        else:
            yield event.plain_result(
                await self._format_action_failure(resolved.request.id, result)
            )

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
            resolved.request,
            event.get_sender_id(),
            reject_reason,
            list_cache=self.ctx.list_cache,
        )
        if result.ok:
            group_label, user_label = await resolve_one_item_labels(
                getattr(self.ctx, "display", None), resolved.request
            )
            yield event.plain_result(
                format_no_result(
                    resolved.request,
                    resolved.index,
                    reject_reason,
                    group_label=group_label,
                    user_label=user_label,
                )
            )
        else:
            yield event.plain_result(
                await self._format_action_failure(resolved.request.id, result)
            )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("mark-external")
    async def audit_mark_external(self, event: AstrMessageEvent, ref: str, confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "mark-external", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result("请使用 /audit mark-external <编号> confirm")
            return
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            ref,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
            allow_stale=True,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        await self.ctx.pipeline.mark_external(
            resolved.request,
            event.get_sender_id(),
            list_cache=self.ctx.list_cache,
        )
        yield event.plain_result(
            format_mark_external_result(resolved.request, resolved.index)
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("dismiss")
    async def audit_dismiss(
        self, event: AstrMessageEvent, ref: str, confirm: str = "", reason: str = ""
    ):
        allowed, message = can_run_command(self._settings(), "dismiss", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        has_confirm, parsed_reason = parse_dismiss_command(event.message_str or "", ref)
        if confirm == "confirm":
            has_confirm = True
        if not has_confirm:
            yield event.plain_result("请使用 /audit dismiss <编号> confirm <原因>")
            return
        dismiss_reason = (parsed_reason or reason or "").strip()
        if not dismiss_reason:
            yield event.plain_result("原因不能为空。请使用 /audit dismiss <编号> confirm <原因>")
            return
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            ref,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
            for_view=True,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        result = await self.ctx.pipeline.dismiss_pending(
            resolved.request,
            event.get_sender_id(),
            dismiss_reason,
            list_cache=self.ctx.list_cache,
        )
        latest = result.get("request") or resolved.request
        if result.get("already_terminal"):
            yield event.plain_result(
                format_already_terminal_result(latest, resolved.index)
            )
            return
        if result.get("idempotent"):
            yield event.plain_result(
                format_dismiss_result(
                    latest, resolved.index, dismiss_reason, idempotent=True
                )
            )
            return
        if not result.get("ok"):
            yield event.plain_result(
                result.get("error") or "关闭失败。"
            )
            return
        yield event.plain_result(
            format_dismiss_result(latest, resolved.index, dismiss_reason)
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("sweep")
    async def audit_sweep(
        self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""
    ):
        allowed, message = can_run_command(self._settings(), "dismiss", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        action, reason = parse_sweep_command(event.message_str or "", arg1, arg2)
        if action == "help":
            yield event.plain_result(format_sweep_help())
            return
        if action == "bad_usage":
            yield event.plain_result(
                "请使用 /audit sweep preview 或 /audit sweep confirm <原因>"
            )
            return
        if action == "need_reason":
            yield event.plain_result(
                "原因不能为空。请使用 /audit sweep confirm <原因>"
            )
            return
        if action == "preview":
            preview = await collect_sweep_preview(self.ctx.pipeline)
            yield event.plain_result(format_sweep_preview(preview))
            return
        result = await run_sweep(
            pipeline=self.ctx.pipeline,
            admin_user_id=event.get_sender_id(),
            reason=reason,
            list_cache=self.ctx.list_cache,
            audit_log=self.ctx.audit,
        )
        yield event.plain_result(format_sweep_result(result))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("stale")
    async def audit_stale(self, event: AstrMessageEvent, limit: int = 10):
        allowed, message = can_run_command(self._settings(), "stale", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        items, index_map = await _fetch_stale_for_admin(self.ctx, event.get_sender_id(), limit)
        yield event.plain_result(_format_stale_list(items, index_map))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("restore")
    async def audit_restore(self, event: AstrMessageEvent, ref: str, confirm: str = ""):
        allowed, message = can_run_command(self._settings(), "restore", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if confirm != "confirm":
            yield event.plain_result("请使用 /audit restore <编号> confirm")
            return
        resolved = await resolve_request_ref(
            event.get_sender_id(),
            ref,
            list_cache=self.ctx.list_cache,
            requests=self.ctx.requests,
            for_restore=True,
        )
        if not resolved.ok:
            yield event.plain_result(resolved.message)
            return
        await self.ctx.pipeline.restore_stale(resolved.request, event.get_sender_id())
        yield event.plain_result(
            format_restore_result(resolved.request, resolved.index)
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("cleanup")
    async def audit_cleanup(self, event: AstrMessageEvent, kind: str = ""):
        allowed, message = can_run_command(self._settings(), "cleanup", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        if kind != "failed":
            yield event.plain_result("用法：/audit cleanup failed")
            return
        items = await self.ctx.requests.list_retryable_failures(limit=20)
        if not items:
            yield event.plain_result("没有疑似失败/可重试的 pending 项。")
            return
        lines = ["疑似失败或可重试的申请：", ""]
        for idx, item in enumerate(items, start=1):
            summary = item.parsed.get("name") if item.parsed else item.user_id
            sid = (item.parsed or {}).get("student_id") or ""
            label = f"{summary} {sid}".strip()
            lines.append(f"[{idx}] {label or item.id} 群={item.group_id} status={item.status}")
            if item.last_action_result and not item.last_action_result.ok:
                lines.append("    上次操作失败，可 /audit ok/no 重试或 mark-external confirm")
        lines.append("")
        lines.append("提示：先 /audit list 获取当前编号后再操作。")
        yield event.plain_result("\n".join(lines))

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
        action = (action or "").strip().lower()
        if action == "status":
            sync_state = self.ctx.cache.load_sync_state()
            yield event.plain_result(
                self.ctx.sync_scheduler.format_status(self._settings(), sync_state)
            )
            return
        if action in {"grad", "graduate", "研究生"}:
            result = await self.ctx.run_grad_sync(source="manual")
            yield event.plain_result(result)
            return
        if action in {"undergraduate", "undergrad", "本科"}:
            result = await self.ctx.run_sync(source="manual")
            yield event.plain_result(result)
            return
        result = await self.ctx.run_sync(source="manual")
        yield event.plain_result(result)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @audit.command("sync-grad")
    async def audit_sync_grad(self, event: AstrMessageEvent):
        allowed, message = can_run_command(self._settings(), "sync", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        result = await self.ctx.run_grad_sync(source="manual")
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
            preview = await self.ctx.release_service.preview(
                self.ctx.requests,
                settings,
                pipeline=self.ctx.pipeline,
            )
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
    @audit.command("catchup")
    async def audit_catchup(
        self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""
    ):
        allowed, message = can_run_command(self._settings(), "release", event)
        if not allowed:
            yield event.plain_result(message)
            return
        await self._record_admin_session(event)
        settings = self._settings()
        if not arg1:
            yield event.plain_result(format_catchup_help(settings))
            return
        if arg1 == "preview":
            preview = await self.ctx.release_service.catchup_preview(
                run_sync=self.ctx.run_sync,
                pipeline=self.ctx.pipeline,
                requests_store=self.ctx.requests,
                settings=settings,
                cache=self.ctx.cache,
            )
            yield event.plain_result(format_catchup_preview(preview, settings))
            return
        # /audit catchup confirm  |  /audit catchup 10 confirm  |  /audit catchup all confirm
        if arg1 == "confirm" and not arg2:
            count = None
        elif arg2 == "confirm":
            if arg1 == "all":
                count = None
            else:
                try:
                    count = max(1, int(arg1))
                except ValueError:
                    yield event.plain_result(
                        "请使用 /audit catchup preview 或 /audit catchup [数量|all] confirm"
                    )
                    return
        else:
            yield event.plain_result(
                "请使用 /audit catchup preview 或 /audit catchup [数量|all] confirm"
            )
            return
        result = await self.ctx.release_service.catchup_batch(
            run_sync=self.ctx.run_sync,
            pipeline=self.ctx.pipeline,
            requests_store=self.ctx.requests,
            settings=settings,
            cache=self.ctx.cache,
            admin_user_id=event.get_sender_id(),
            count=count,
            audit_log=self.ctx.audit,
        )
        yield event.plain_result(format_catchup_result(result, settings))

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
        group_labels, user_labels = await resolve_display_labels(
            getattr(self.ctx, "display", None), items
        )
        yield event.plain_result(
            format_list(
                items,
                index_map,
                group_labels=group_labels,
                user_labels=user_labels,
            )
        )

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
        group_label, user_label = await resolve_one_item_labels(
            getattr(self.ctx, "display", None), resolved.request
        )
        yield event.plain_result(
            format_view(
                resolved.request,
                resolved.index,
                group_label=group_label,
                user_label=user_label,
            )
        )

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
        result = await self.ctx.pipeline.admin_approve(
            resolved.request,
            event.get_sender_id(),
            list_cache=self.ctx.list_cache,
        )
        if result.ok:
            group_label, user_label = await resolve_one_item_labels(
                getattr(self.ctx, "display", None), resolved.request
            )
            yield event.plain_result(
                format_ok_result(
                    resolved.request,
                    resolved.index,
                    group_label=group_label,
                    user_label=user_label,
                )
            )
        else:
            yield event.plain_result(
                await self._format_action_failure(resolved.request.id, result)
            )

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
            resolved.request,
            event.get_sender_id(),
            "管理员人工拒绝",
            list_cache=self.ctx.list_cache,
        )
        if result.ok:
            group_label, user_label = await resolve_one_item_labels(
                getattr(self.ctx, "display", None), resolved.request
            )
            yield event.plain_result(
                format_no_result(
                    resolved.request,
                    resolved.index,
                    "管理员人工拒绝",
                    group_label=group_label,
                    user_label=user_label,
                )
            )
        else:
            yield event.plain_result(
                await self._format_action_failure(resolved.request.id, result)
            )

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
