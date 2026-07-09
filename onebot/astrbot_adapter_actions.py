from __future__ import annotations

import logging
from typing import Any

from config import PluginSettings, redact_tokens_in_string
from data_source.students import ActionResult

logger = logging.getLogger(__name__)

AIOHTTP_PLATFORM_NAMES = frozenset({"aiocqhttp", "onebot", "napcat", "lagrange"})


class AstrBotAdapterActionClient:
    def __init__(self, astrbot_context: Any, settings: PluginSettings) -> None:
        self.astrbot_context = astrbot_context
        self.settings = settings
        self._adapter_available: str | None = None
        self._platform_id: str | None = None
        self._event_bot: Any | None = None

    def backend_name(self) -> str:
        return "astrbot_adapter"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def remember_event(self, event: Any) -> None:
        bot = getattr(event, "bot", None)
        if bot is not None and hasattr(bot, "api"):
            self._event_bot = bot
        platform_id = _safe_call(event, "get_platform_id")
        if platform_id:
            self._platform_id = str(platform_id)

    def restore_hints(self, *, platform_id: str | None = None, event_bot: Any | None = None) -> None:
        if platform_id:
            self._platform_id = platform_id
        if event_bot is not None and hasattr(event_bot, "api"):
            self._event_bot = event_bot

    def _iter_platforms(self) -> list[Any]:
        pm = getattr(self.astrbot_context, "platform_manager", None)
        if pm is None:
            return []
        if hasattr(pm, "platform_insts"):
            return list(pm.platform_insts)
        if hasattr(pm, "get_insts"):
            try:
                return list(pm.get_insts())
            except Exception:
                return []
        return []

    def _platform_name(self, platform: Any) -> str:
        meta = platform.meta() if hasattr(platform, "meta") else None
        if meta is None:
            return ""
        return str(getattr(meta, "name", "") or getattr(meta, "id", "") or "").lower()

    def _client_from_platform(self, platform: Any) -> Any | None:
        if not hasattr(platform, "get_client"):
            return None
        try:
            client = platform.get_client()
            if client is not None and hasattr(client, "api"):
                return client
        except Exception as exc:
            logger.debug("[audit] platform.get_client failed: %s", exc)
        return None

    async def _get_bot_client(self, event: Any | None = None) -> Any | None:
        if event is not None:
            self.remember_event(event)

        if self._event_bot is not None and hasattr(self._event_bot, "api"):
            return self._event_bot

        if self._platform_id and hasattr(self.astrbot_context, "get_platform_inst"):
            try:
                platform = self.astrbot_context.get_platform_inst(self._platform_id)
                client = self._client_from_platform(platform) if platform else None
                if client is not None:
                    return client
            except Exception as exc:
                logger.debug("[audit] get_platform_inst(%s) failed: %s", self._platform_id, exc)

        for platform in self._iter_platforms():
            name = self._platform_name(platform)
            if name in AIOHTTP_PLATFORM_NAMES or "cq" in name or "onebot" in name:
                client = self._client_from_platform(platform)
                if client is not None:
                    return client

        for platform in self._iter_platforms():
            client = self._client_from_platform(platform)
            if client is not None:
                return client

        if hasattr(self.astrbot_context, "get_platform"):
            for lookup in _legacy_platform_lookups():
                try:
                    platform = self.astrbot_context.get_platform(lookup)
                except Exception:
                    platform = None
                client = self._client_from_platform(platform) if platform else None
                if client is not None:
                    return client

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

    async def call_action(
        self, action: str, params: dict[str, Any], event: Any | None = None
    ) -> ActionResult:
        client = await self._get_bot_client(event)
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
        event: Any | None = None,
    ) -> ActionResult:
        return await self.call_action(
            "set_group_add_request",
            {
                "flag": flag,
                "sub_type": sub_type,
                "approve": approve,
                "reason": reason,
            },
            event=event,
        )

    async def get_login_info(self, event: Any | None = None) -> ActionResult:
        return await self.call_action("get_login_info", {}, event=event)

    async def get_group_list(self, event: Any | None = None) -> ActionResult:
        return await self.call_action("get_group_list", {}, event=event)

    async def send_private_msg_safe(self, user_id: str, message: str) -> ActionResult:
        return ActionResult(
            ok=False,
            message="send_private_msg not supported on astrbot_adapter backend; use context.send_message",
        )

    async def probe_api(self, event: Any | None = None) -> dict[str, Any]:
        client = await self._get_bot_client(event)
        if client is None:
            platforms = [self._platform_name(p) for p in self._iter_platforms()]
            detail = f"aiocqhttp adapter not found; platforms={platforms or '(none)'}"
            return {
                "adapter_found": "no",
                "adapter_action_available": "no",
                "test_action": "",
                "result": "failed",
                "message": detail,
                "platform_id": self._platform_id or "(unset)",
            }

        for action in ("get_login_info", "get_group_list"):
            result = await self.call_action(action, {}, event=event)
            if result.ok:
                self._adapter_available = "yes"
                probe: dict[str, Any] = {
                    "adapter_found": "yes",
                    "adapter_action_available": "yes",
                    "test_action": action,
                    "result": "ok",
                    "message": result.message or "ok",
                    "platform_id": self._platform_id or "(from event/platform scan)",
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
            "platform_id": self._platform_id or "(from event/platform scan)",
        }

    def cached_adapter_available(self) -> str:
        return self._adapter_available or "unknown"


def _safe_call(obj: Any, method: str) -> Any | None:
    fn = getattr(obj, method, None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def _legacy_platform_lookups() -> list[Any]:
    lookups: list[Any] = ["aiocqhttp"]
    try:
        from astrbot.api.event import filter as api_filter

        lookups.append(api_filter.PlatformAdapterType.AIOCQHTTP)
    except Exception:
        pass
    return lookups
