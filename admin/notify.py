from __future__ import annotations

from typing import Any, Callable

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - unit tests without astrbot
    import logging

    logger = logging.getLogger(__name__)

from admin.ux_formatter import (
    format_auto_result_notice,
    format_blacklist_reject_notice,
    extract_external_applicant_and_verification,
    format_manual_review_notice,
    format_external_handled_notice,
    format_pending_comment_updated_notice,
    resolve_external_notice_labels,
)
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
        display: Any | None = None,
    ) -> None:
        self.settings = settings
        self.actions = actions
        self.astrbot_context = astrbot_context
        self.admin_sessions = admin_sessions
        self._http_notify_client_getter = http_notify_client_getter
        self.list_cache = list_cache
        self.display = display

    def reload_settings(
        self,
        settings: PluginSettings,
        actions: ActionClient,
        astrbot_context: Any,
        admin_sessions: Any,
        http_notify_client_getter: Callable[[], ActionClient | None],
        list_cache: Any | None = None,
        display: Any | None = None,
    ) -> None:
        self.settings = settings
        self.actions = actions
        self.astrbot_context = astrbot_context
        self.admin_sessions = admin_sessions
        self._http_notify_client_getter = http_notify_client_getter
        if list_cache is not None:
            self.list_cache = list_cache
        if display is not None:
            self.display = display
        elif self.display is not None and hasattr(self.display, "set_actions"):
            self.display.set_actions(actions)

    async def _resolve_labels(
        self,
        *,
        group_id: str,
        user_id: str,
        parsed: dict | None = None,
        group_label: str | None = None,
        user_label: str | None = None,
    ) -> tuple[str | None, str | None]:
        if self.display is None:
            return group_label, user_label
        try:
            if group_label is None:
                group_label = await self.display.get_group_label(group_id)
        except Exception:
            logger.debug("[audit] resolve group_label failed", exc_info=True)
        try:
            if user_label is None:
                user_label = await self.display.get_user_label(
                    group_id, user_id, parsed
                )
        except Exception:
            logger.debug("[audit] resolve user_label failed", exc_info=True)
        return group_label, user_label

    async def notify_manual_review(
        self,
        *,
        request_id: str,
        group_id: str,
        user_id: str,
        comment: str,
        parsed: dict,
        reason: str,
        summary: str | None = None,
        group_label: str | None = None,
        user_label: str | None = None,
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
        group_label, user_label = await self._resolve_labels(
            group_id=group_id,
            user_id=user_id,
            parsed=parsed,
            group_label=group_label,
            user_label=user_label,
        )
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
                profile=str((parsed or {}).get("_profile") or "undergraduate"),
                parsed=parsed,
                summary=summary,
                group_label=group_label,
                user_label=user_label,
            )
            if await self._send_to_admin(admin_id, message):
                sent_count += 1
            else:
                logger.warning(
                    "[audit] 无法通知管理员 %s：adapter/UMO/HTTP 均失败。"
                    "请检查 /audit debug 中 adapter 与 admin_notify_channels。",
                    admin_id,
                )
        logger.info(
            "[audit] manual_review notify request=%s sent=%s/%s",
            request_id,
            sent_count,
            len(targets),
        )

    async def notify_pending_comment_updated(
        self,
        *,
        request_id: str,
        group_id: str,
        user_id: str,
        comment: str,
        reason: str,
        summary: str | None = None,
        group_label: str | None = None,
        user_label: str | None = None,
        parsed: dict | None = None,
    ) -> None:
        if not self.settings.admin_notify:
            return
        admin_ids = list(self.settings.admin_qq_ids)
        if not admin_ids:
            logger.warning("[audit] pending update notify skipped: admin_qq_ids empty")
            return
        targets = list(admin_ids)
        judgement = reason or "需要人工确认"
        group_label, user_label = await self._resolve_labels(
            group_id=group_id,
            user_id=user_id,
            parsed=parsed,
            group_label=group_label,
            user_label=user_label,
        )
        sent_count = 0
        logger.info(
            "[audit] pending update notify request=%s targets=%s",
            request_id,
            targets,
        )
        for admin_id in targets:
            index = None
            if self.list_cache is not None:
                index = await self.list_cache.append(admin_id, request_id)
            message = format_pending_comment_updated_notice(
                index=index,
                group_id=group_id,
                user_id=user_id,
                comment=comment,
                judgement=judgement,
                summary=summary,
                group_label=group_label,
                user_label=user_label,
            )
            if await self._send_to_admin(admin_id, message):
                sent_count += 1
            else:
                logger.warning(
                    "[audit] 无法通知管理员 %s：adapter/UMO/HTTP 均失败。",
                    admin_id,
                )
        logger.info(
            "[audit] pending update notify request=%s sent=%s/%s",
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
        summary: str | None = None,
        comment: str | None = None,
        match_strength: str | None = None,
        action_message: str | None = None,
        group_label: str | None = None,
        user_label: str | None = None,
        parsed: dict | None = None,
    ) -> None:
        if not self.settings.admin_notify:
            return
        group_label, user_label = await self._resolve_labels(
            group_id=group_id,
            user_id=user_id,
            parsed=parsed,
            group_label=group_label,
            user_label=user_label,
        )
        message = format_auto_result_notice(
            request_id=request_id,
            group_id=group_id,
            user_id=user_id,
            ok=ok,
            reason=reason,
            summary=summary,
            comment=comment,
            match_strength=match_strength,
            action_message=action_message,
            group_label=group_label,
            user_label=user_label,
        )
        await self._notify_admins(message, exclude_user_id=user_id)

    async def notify_blacklist_reject_result(
        self,
        *,
        request_id: str,
        group_id: str,
        user_id: str,
        ok: bool,
        reason: str,
        reject_reason: str,
        summary: str | None = None,
        comment: str | None = None,
        action_message: str | None = None,
        group_label: str | None = None,
        user_label: str | None = None,
        parsed: dict | None = None,
    ) -> None:
        if not self.settings.admin_notify:
            return
        group_label, user_label = await self._resolve_labels(
            group_id=group_id,
            user_id=user_id,
            parsed=parsed,
            group_label=group_label,
            user_label=user_label,
        )
        message = format_blacklist_reject_notice(
            request_id=request_id,
            group_id=group_id,
            user_id=user_id,
            ok=ok,
            reason=reason,
            reject_reason=reject_reason,
            summary=summary,
            comment=comment,
            action_message=action_message,
            group_label=group_label,
            user_label=user_label,
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
        # Pure display formatting; never leak internal state-machine fields.
        short_id = request_id[:12] if request_id else ""
        comment_line = (comment or "")[:120]
        group_label, user_label, operator_label = await resolve_external_notice_labels(
            self.display,
            group_id=group_id,
            user_id=user_id,
            operator_id=operator_id,
        )
        applicant, verification = extract_external_applicant_and_verification(
            summary=summary,
            comment=comment_line,
            user_id=user_id,
        )
        message = format_external_handled_notice(
            request_id=request_id,
            applicant=applicant,
            verification=verification,
            group_label=group_label,
            user_label=user_label,
            operator_label=operator_label,
        )
        admin_ids = list(self.settings.admin_qq_ids)
        if not admin_ids:
            logger.warning("[audit] external notify skipped: admin_qq_ids empty")
            return
        sent_count = 0
        logger.info(
            "[audit] external notify request=%s targets=%s",
            short_id,
            admin_ids,
        )
        for admin_id in admin_ids:
            if await self._send_to_admin(admin_id, message):
                sent_count += 1
            else:
                logger.warning(
                    "[audit] 无法通知管理员 %s：adapter/UMO/HTTP 均失败。",
                    admin_id,
                )
        logger.info(
            "[audit] external notify request=%s sent=%s/%s",
            short_id,
            sent_count,
            len(admin_ids),
        )

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
                    "[audit] 无法通知管理员 %s：adapter/UMO/HTTP 均失败。"
                    "请检查 /audit debug 中 adapter 与 admin_notify_channels。",
                    admin_id,
                )

    async def _send_to_admin(self, admin_id: str, message: str) -> bool:
        umo = self.admin_sessions.get_umo(admin_id)
        if umo:
            if await self._send_via_umo(admin_id, umo, message):
                return True

        if await self._send_via_adapter(admin_id, message):
            return True

        http_client = self._http_notify_client_getter()
        if http_client is not None:
            try:
                result = await http_client.send_private_msg_safe(admin_id, message)
                if result.ok:
                    logger.info("[audit] notify via HTTP send_private_msg admin=%s", admin_id)
                    return True
                logger.warning(
                    "[audit] HTTP send_private_msg 失败 admin=%s: %s",
                    admin_id,
                    result.message,
                )
            except Exception as exc:
                logger.warning("[audit] HTTP send_private_msg 异常 admin=%s: %s", admin_id, exc)
        return False

    async def _send_via_umo(self, admin_id: str, umo: str, message: str) -> bool:
        try:
            from astrbot.api.event import MessageChain

            ok = await self.astrbot_context.send_message(
                umo, MessageChain().message(message)
            )
            if ok:
                logger.info("[audit] notify via UMO send_message admin=%s umo=%s", admin_id, umo)
                return True
            logger.warning(
                "[audit] context.send_message 返回 false admin=%s umo=%s",
                admin_id,
                umo,
            )
        except Exception as exc:
            logger.warning("[audit] context.send_message 失败 admin=%s: %s", admin_id, exc)
        return False

    async def _send_via_adapter(self, admin_id: str, message: str) -> bool:
        try:
            result = await self.actions.send_private_msg_safe(admin_id, message)
            if result.ok:
                logger.info("[audit] notify via adapter send_private_msg admin=%s", admin_id)
                return True
            logger.warning(
                "[audit] adapter send_private_msg 失败 admin=%s: %s",
                admin_id,
                result.message,
            )
        except Exception as exc:
            logger.warning(
                "[audit] adapter send_private_msg 异常 admin=%s: %s",
                admin_id,
                exc,
            )
        return False
