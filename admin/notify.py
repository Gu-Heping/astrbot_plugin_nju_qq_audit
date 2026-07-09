from __future__ import annotations

from config import PluginSettings
from onebot.http_actions import OneBotHttpActions


class AdminNotifier:
    def __init__(self, settings: PluginSettings, actions: OneBotHttpActions) -> None:
        self.settings = settings
        self.actions = actions

    def reload_settings(self, settings: PluginSettings) -> None:
        self.settings = settings

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
        summary = comment[:80]
        message = "\n".join(
            [
                "[入群审核] 需人工复核",
                f"request_id: {request_id}",
                f"group_id: {group_id}",
                f"user_id: {user_id}",
                f"comment: {summary}",
                f"parsed: {parsed}",
                f"decision/reason: {reason}",
            ]
        )
        await self._notify_admins(message, exclude_user_id=user_id)

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

    async def _notify_admins(self, message: str, exclude_user_id: str | None = None) -> None:
        for admin_id in self.settings.admin_qq_ids:
            if exclude_user_id and admin_id == exclude_user_id:
                continue
            await self.actions.send_private_msg_safe(admin_id, message)
