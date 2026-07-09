from __future__ import annotations

from config import PluginSettings


def is_private_message(event) -> bool:
    msg_type = event.get_message_type()
    name = getattr(msg_type, "name", str(msg_type))
    return name == "FRIEND_MESSAGE"


def is_admin(settings: PluginSettings, sender_id: str) -> bool:
    if not settings.admin_qq_ids:
        return False
    return sender_id in settings.admin_qq_ids


def admin_configured(settings: PluginSettings) -> bool:
    return bool(settings.admin_qq_ids)


def can_run_command(settings: PluginSettings, command: str, event) -> tuple[bool, str]:
    if not is_private_message(event):
        return False, "请私聊机器人使用"

    sender_id = event.get_sender_id()
    if is_admin(settings, sender_id):
        return True, ""

    debug_commands = {
        "help",
        "status",
        "probe",
        "probe_status",
        "probe_last",
    }
    if not admin_configured(settings) and command in debug_commands:
        return True, ""

    if not admin_configured(settings):
        return False, "未配置管理员（admin_qq_ids 为空），此命令不可用。"

    return False, "无权限"
