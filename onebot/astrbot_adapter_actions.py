from __future__ import annotations

from typing import Any

from config import PluginSettings, redact_tokens_in_string
from data_source.students import ActionResult


class AstrBotAdapterActionClient:
    def __init__(self, astrbot_context: Any, settings: PluginSettings) -> None:
        self.astrbot_context = astrbot_context
        self.settings = settings
        self._adapter_available: str | None = None

    def backend_name(self) -> str:
        return "astrbot_adapter"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def _get_bot_client(self) -> Any | None:
        try:
            from astrbot.api.event import filter as api_filter
            from astrbot.api.platform import AiocqhttpAdapter

            platform = self.astrbot_context.get_platform(
                api_filter.PlatformAdapterType.AIOCQHTTP
            )
            if platform is None:
                return None
            if not isinstance(platform, AiocqhttpAdapter):
                return None
            return platform.get_client()
        except Exception:
            return None

    def _normalize_response(self, action: str, response: Any) -> ActionResult:
        if isinstance(response, dict):
            status = str(response.get("status", ""))
            retcode = response.get("retcode")
            message = response.get("message")
            data = response.get("data")
            ok = status == "ok" or retcode == 0
            if ok:
                detail = str(data) if data is not None else str(message or "ok")
                return ActionResult(
                    ok=True,
                    retcode=int(retcode) if retcode is not None else 0,
                    message=redact_tokens_in_string(detail, self.settings),
                    data=data if isinstance(data, dict) else None,
                )
            return ActionResult(
                ok=False,
                retcode=int(retcode) if retcode is not None else None,
                message=redact_tokens_in_string(
                    str(message or f"{action} failed"), self.settings
                ),
            )
        return ActionResult(ok=True, retcode=0, message="ok")

    async def call_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        client = await self._get_bot_client()
        if client is None:
            return ActionResult(
                ok=False,
                message="aiocqhttp adapter not available",
            )
        try:
            response = await client.api.call_action(action, **params)
            return self._normalize_response(action, response)
        except Exception as exc:
            return ActionResult(
                ok=False,
                message=redact_tokens_in_string(str(exc), self.settings),
            )

    async def set_group_add_request(
        self,
        flag: str,
        sub_type: str,
        approve: bool,
        reason: str = "",
    ) -> ActionResult:
        return await self.call_action(
            "set_group_add_request",
            {
                "flag": flag,
                "sub_type": sub_type,
                "approve": approve,
                "reason": reason,
            },
        )

    async def get_login_info(self) -> ActionResult:
        return await self.call_action("get_login_info", {})

    async def get_group_list(self) -> ActionResult:
        return await self.call_action("get_group_list", {})

    async def send_private_msg_safe(self, user_id: str, message: str) -> ActionResult:
        return ActionResult(
            ok=False,
            message="send_private_msg not supported on astrbot_adapter backend; use context.send_message",
        )

    async def probe_api(self) -> dict[str, Any]:
        client = await self._get_bot_client()
        if client is None:
            return {
                "adapter_found": "no",
                "adapter_action_available": "no",
                "test_action": "",
                "result": "failed",
                "message": "aiocqhttp adapter not found",
            }

        for action in ("get_login_info", "get_group_list"):
            result = await self.call_action(action, {})
            if result.ok:
                self._adapter_available = "yes"
                probe: dict[str, Any] = {
                    "adapter_found": "yes",
                    "adapter_action_available": "yes",
                    "test_action": action,
                    "result": "ok",
                    "message": result.message or "ok",
                }
                if action == "get_login_info" and isinstance(result.data, dict):
                    if result.data.get("user_id") is not None:
                        probe["user_id"] = result.data.get("user_id")
                    if result.data.get("nickname"):
                        probe["nickname"] = result.data.get("nickname")
                return probe

        self._adapter_available = "no"
        return {
            "adapter_found": "yes",
            "adapter_action_available": "no",
            "test_action": "get_login_info",
            "result": "failed",
            "message": redact_tokens_in_string(
                "get_login_info and get_group_list both failed", self.settings
            ),
        }

    def cached_adapter_available(self) -> str:
        return self._adapter_available or "unknown"
