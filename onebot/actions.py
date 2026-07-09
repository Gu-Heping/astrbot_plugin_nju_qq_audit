from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from config import PluginSettings
from data_source.students import ActionResult


@runtime_checkable
class ActionClient(Protocol):
    settings: PluginSettings

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def call_action(self, action: str, params: dict[str, Any]) -> ActionResult: ...

    async def set_group_add_request(
        self,
        flag: str,
        sub_type: str,
        approve: bool,
        reason: str = "",
    ) -> ActionResult: ...

    async def get_login_info(self) -> ActionResult: ...

    async def get_group_list(self) -> ActionResult: ...

    async def send_private_msg_safe(self, user_id: str, message: str) -> ActionResult: ...

    def backend_name(self) -> str: ...


def create_action_client(astrbot_context: Any, settings: PluginSettings) -> ActionClient:
    if settings.onebot_action_backend == "http":
        from onebot.http_actions import HttpActionClient

        return HttpActionClient(settings)
    from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient

    return AstrBotAdapterActionClient(astrbot_context, settings)


def create_http_notify_client(settings: PluginSettings) -> ActionClient | None:
    if not settings.onebot_http_url:
        return None
    from onebot.http_actions import HttpActionClient

    return HttpActionClient(settings)
