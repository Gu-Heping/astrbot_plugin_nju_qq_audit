from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from config import PluginSettings, redact_tokens_in_string
from data_source.students import ActionResult


def build_action_url(base_url: str, action: str) -> str:
    return f"{base_url.rstrip('/')}/{action.lstrip('/')}"


def _is_retryable_http_status(status: int) -> bool:
    if status in {400, 401, 403, 404}:
        return False
    if status == 408 or status >= 500:
        return True
    return False


class HttpActionClient:
    def __init__(self, settings: PluginSettings) -> None:
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None

    def backend_name(self) -> str:
        return "http"

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def call_action(
        self, action: str, params: dict[str, Any]
    ) -> ActionResult:
        if not self.settings.onebot_http_url:
            return ActionResult(
                ok=False,
                message="HTTP backend enabled but onebot_http_url is empty",
            )
        await self.start()
        assert self._session is not None
        last_result = ActionResult(ok=False, message="not attempted")
        for attempt in range(self.settings.http_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self.settings.http_retry_delay_ms / 1000)
            last_result = await self._call_action_once(action, params)
            if last_result.ok:
                return last_result
            retryable = last_result.retcode is None or (
                last_result.retcode != 0 and _is_retryable_http_status(last_result.retcode or 0)
            )
            if attempt >= self.settings.http_retries or not retryable:
                break
        return last_result

    async def _call_action_once(self, action: str, params: dict[str, Any]) -> ActionResult:
        assert self._session is not None
        url = build_action_url(self.settings.onebot_http_url, action)
        headers = {"Content-Type": "application/json"}
        if self.settings.onebot_access_token:
            headers["Authorization"] = f"Bearer {self.settings.onebot_access_token}"
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_ms / 1000)
        try:
            async with self._session.post(url, json=params, headers=headers, timeout=timeout) as resp:
                http_status = resp.status
                try:
                    body = await resp.json()
                except aiohttp.ContentTypeError:
                    text = await resp.text()
                    return ActionResult(
                        ok=False,
                        retcode=http_status,
                        message=redact_tokens_in_string(
                            f"Invalid JSON response from {action}: {text[:200]}",
                            self.settings,
                        ),
                    )
                status = str(body.get("status", ""))
                retcode = int(body.get("retcode", -1))
                message = body.get("message")
                business_ok = 200 <= http_status < 300 and status == "ok" and retcode == 0
                msg = redact_tokens_in_string(str(message or ""), self.settings)
                if business_ok:
                    return ActionResult(ok=True, retcode=retcode, message=msg or "ok")
                return ActionResult(
                    ok=False,
                    retcode=retcode if retcode >= 0 else http_status,
                    message=msg or redact_tokens_in_string(f"HTTP {http_status} action failed", self.settings),
                )
        except asyncio.TimeoutError:
            return ActionResult(ok=False, message="timeout")
        except aiohttp.ClientError as exc:
            return ActionResult(
                ok=False,
                message=redact_tokens_in_string(str(exc), self.settings),
            )

    async def send_private_msg(self, user_id: str, message: str) -> ActionResult:
        return await self.call_action(
            "send_private_msg",
            {"user_id": int(user_id), "message": message},
        )

    async def send_private_msg_safe(self, user_id: str, message: str) -> ActionResult:
        try:
            return await self.send_private_msg(user_id, message)
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


# Backward compatibility alias for tests/imports
OneBotHttpActions = HttpActionClient
