from __future__ import annotations

import logging
from typing import Any, Callable

from admin.ux_formatter import format_manual_review_notice
from config import PluginSettings
from onebot.actions import ActionClient

logger = logging.getLogger(__name__)


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
            return
        judgement = reason or "需要人工确认"
        for admin_id in self.settings.admin_qq_ids:
            if user_id and admin_id == user_id:
                continue
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
            sent = await self._send_to_admin(admin_id, message)
            if not sent:
                logger.warning(
                    "[audit] 无法通知管理员 %s：无 UMO 且 HTTP fallback 不可用。"
                    "请管理员私聊 /audit 建立通知通道。",
                    admin_id,
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
        group_id: str,
        user_id: str,
        summary: str | None = None,
    ) -> None:
        if not self.settings.admin_notify:
            return
        label = summary or user_id
        message = "\n".join(
            [
                "[入群审核] 申请已在 QQ 侧通过",
                f"群：{group_id}",
                f"用户：{user_id}",
                f"摘要：{label}",
                "队列已标记为 external。",
            ]
        )
        await self._notify_admins(message, exclude_user_id=user_id)

    async def _notify_admins(self, message: str, exclude_user_id: str | None = None) -> None:
        for admin_id in self.settings.admin_qq_ids:
            if exclude_user_id and admin_id == exclude_user_id:
                continue
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
