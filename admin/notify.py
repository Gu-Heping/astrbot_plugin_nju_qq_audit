from __future__ import annotations

from typing import Any, Callable

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - unit tests without astrbot
    import logging

    logger = logging.getLogger(__name__)

from admin.ux_formatter import format_manual_review_notice
from config import PluginSettings
from onebot.actions import ActionClient


def _resolve_notify_targets(
    admin_ids: list[str], exclude_user_id: str | None = None
) -> list[str]:
    if not admin_ids:
        return []
    if not exclude_user_id:
        return list(admin_ids)
    filtered = [admin_id for admin_id in admin_ids if admin_id != exclude_user_id]
    return filtered if filtered else list(admin_ids)


class AdminNotifier:
    def __init__(
        self,
        settings: PluginSettings,
        actions: ActionClient,
        astrbot_context: Any,
        admin_sessions: Any,
        http_notify_client_getter: Callable[[], ActionClient | None],
        list_cache: Any | None = None,
    ) -> None:
        self.settings = settings
        self.actions = actions
        self.astrbot_context = astrbot_context
        self.admin_sessions = admin_sessions
        self._http_notify_client_getter = http_notify_client_getter
        self.list_cache = list_cache

    def reload_settings(
        self,
        settings: PluginSettings,
        actions: ActionClient,
        astrbot_context: Any,
        admin_sessions: Any,
        http_notify_client_getter: Callable[[], ActionClient | None],
        list_cache: Any | None = None,
    ) -> None:
        self.settings = settings
        self.actions = actions
        self.astrbot_context = astrbot_context
        self.admin_sessions = admin_sessions
        self._http_notify_client_getter = http_notify_client_getter
        if list_cache is not None:
            self.list_cache = list_cache

    async def notify_manual_review(
        self,
        *,
        request_id: str,
        group_id: str,
        user_id: str,
        comment: str,
        parsed: dict,
        reason: str,
    ) -> None:
        if not self.settings.admin_notify:
            logger.debug("[audit] manual_review notify skipped: admin_notify=false")
            return
        admin_ids = list(self.settings.admin_qq_ids)
        if not admin_ids:
            logger.warning("[audit] manual_review notify skipped: admin_qq_ids empty")
            return
        targets = _resolve_notify_targets(admin_ids, user_id)
        judgement = reason or "需要人工确认"
        sent_count = 0
        logger.info(
            "[audit] manual_review notify request=%s targets=%s",
            request_id,
            targets,
        )
        for admin_id in targets:
            index = None
            if self.list_cache is not None:
                index = await self.list_cache.append(admin_id, request_id)
            message = format_manual_review_notice(
                index=index,
                group_id=group_id,
                user_id=user_id,
                comment=comment,
                judgement=judgement,
            )
            if await self._send_to_admin(admin_id, message):
                sent_count += 1
            else:
                logger.warning(
                    "[audit] 无法通知管理员 %s：无 UMO 且 HTTP fallback 不可用。"
                    "请管理员私聊 /audit 建立通知通道。",
                    admin_id,
                )
        logger.info(
            "[audit] manual_review notify request=%s sent=%s/%s",
            request_id,
            sent_count,
            len(targets),
        )

    async def notify_auto_result(
        self,
        *,
        request_id: str,
        group_id: str,
        user_id: str,
        ok: bool,
        reason: str,
    ) -> None:
        if not self.settings.admin_notify:
            return
        message = "\n".join(
            [
                f"[入群审核] 自动通过{'成功' if ok else '失败'}",
                f"request_id: {request_id}",
                f"group_id: {group_id}",
                f"user_id: {user_id}",
                f"reason: {reason}",
            ]
        )
        await self._notify_admins(message, exclude_user_id=user_id)

    async def notify_external_handled(
        self,
        *,
        request_id: str,
        group_id: str,
        user_id: str,
        summary: str | None = None,
        comment: str | None = None,
        operator_id: str | None = None,
        notice_sub_type: str | None = None,
    ) -> None:
        if not self.settings.admin_notify:
            return
        short_id = request_id[:12] if request_id else ""
        label = summary or user_id
        comment_line = (comment or "")[:80]
        lines = [
            "[入群审核] 入群申请已在 QQ 侧通过/入群，队列已标记为 external。",
            f"申请：{short_id}",
            f"群：{group_id}",
            f"用户：{user_id}",
            f"摘要：{label}",
        ]
        if comment_line:
            lines.append(f"验证：{comment_line}")
        if notice_sub_type:
            lines.append(
                f"QQ 事件 sub_type：{notice_sub_type}"
                "（OneBot 通知类型；搜索入群、邀请、审批通过均可能为 invite，不代表实际入群路径）"
            )
        if operator_id:
            lines.append(f"操作者 QQ：{operator_id}")
        await self._notify_admins("\n".join(lines), exclude_user_id=None)

    async def notify_stale_request(
        self,
        *,
        request_id: str,
        group_id: str,
        user_id: str,
        reason: str,
        summary: str | None = None,
        comment: str | None = None,
    ) -> None:
        if not self.settings.admin_notify:
            return
        short_id = request_id[:12] if request_id else ""
        label = summary or user_id
        comment_line = (comment or "")[:80]
        lines = [
            "[入群审核] QQ 侧已无此入群申请，队列已标记为 stale。",
            "请到 QQ 群管理后台确认是否已处理或已入群。",
            f"申请：{short_id}",
            f"群：{group_id}",
            f"用户：{user_id}",
            f"摘要：{label}",
            f"原因：{reason[:120]}",
        ]
        if comment_line:
            lines.append(f"验证：{comment_line}")
        await self._notify_admins("\n".join(lines), exclude_user_id=None)

    async def _notify_admins(self, message: str, exclude_user_id: str | None = None) -> None:
        targets = _resolve_notify_targets(list(self.settings.admin_qq_ids), exclude_user_id)
        for admin_id in targets:
            sent = await self._send_to_admin(admin_id, message)
            if not sent:
                logger.warning(
                    "[audit] 无法通知管理员 %s：无 UMO 且 HTTP fallback 不可用。"
                    "请管理员私聊 /audit 建立通知通道。",
                    admin_id,
                )

    async def _send_to_admin(self, admin_id: str, message: str) -> bool:
        umo = self.admin_sessions.get_umo(admin_id)
        if umo:
            try:
                from astrbot.api.event import MessageChain

                ok = await self.astrbot_context.send_message(
                    umo, MessageChain().message(message)
                )
                if ok:
                    return True
            except Exception as exc:
                logger.warning("[audit] context.send_message 失败 admin=%s: %s", admin_id, exc)

        http_client = self._http_notify_client_getter()
        if http_client is not None:
            result = await http_client.send_private_msg_safe(admin_id, message)
            return result.ok
        return False
