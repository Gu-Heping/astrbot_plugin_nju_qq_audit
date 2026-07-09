from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

from config import PluginSettings, redact_tokens_in_string

TOKEN_TTL_SECONDS = int(2.5 * 24 * 60 * 60)
_cached_token: dict[str, Any] | None = None


def clear_base_token_cache() -> None:
    global _cached_token
    _cached_token = None


def build_list_rows_url(server_url: str, dtable_uuid: str, params: dict[str, Any]) -> str:
    gateway = server_url.rstrip("/") + "/api-gateway"
    query = urlencode(params)
    return f"{gateway}/api/v2/dtables/{dtable_uuid}/rows/?{query}"


class NjuTableClient:
    def __init__(self, settings: PluginSettings) -> None:
        self.settings = settings

    async def get_base_token(self, session: aiohttp.ClientSession) -> dict[str, str]:
        global _cached_token
        now = time.time()
        if _cached_token and _cached_token["expires_at"] > now:
            return _cached_token["value"]

        url = self.settings.njutable_server_url.rstrip("/") + "/api/v2.1/dtable/app-access-token/"
        headers = {
            "Authorization": f"Bearer {self.settings.njutable_api_token}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.settings.njutable_timeout_ms / 1000)
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            body_text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    redact_tokens_in_string(
                        f"Base-Token request failed ({resp.status}): {body_text[:200]}",
                        self.settings,
                    )
                )
            data = await resp.json()
        access_token = str(data.get("access_token", ""))
        dtable_uuid = str(data.get("dtable_uuid", ""))
        if not access_token or not dtable_uuid:
            raise RuntimeError("Base-Token response missing access_token or dtable_uuid")
        value = {"access_token": access_token, "dtable_uuid": dtable_uuid}
        _cached_token = {"value": value, "expires_at": now + TOKEN_TTL_SECONDS}
        return value

    async def list_rows_page(
        self,
        session: aiohttp.ClientSession,
        base: dict[str, str],
        *,
        start: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        params = {
            "table_name": self.settings.njutable_table_name,
            "start": start,
            "limit": limit,
            "convert_keys": "true",
        }
        if self.settings.njutable_view_name:
            params["view_name"] = self.settings.njutable_view_name
        url = build_list_rows_url(self.settings.njutable_server_url, base["dtable_uuid"], params)
        headers = {
            "Authorization": f"Bearer {base['access_token']}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.settings.njutable_timeout_ms / 1000)
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            if resp.status == 401:
                clear_base_token_cache()
                raise PermissionError("ROWS_UNAUTHORIZED")
            body_text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    redact_tokens_in_string(
                        f"List rows failed ({resp.status}): {body_text[:200]}",
                        self.settings,
                    )
                )
            data = await resp.json()
        rows = data.get("rows", [])
        return rows if isinstance(rows, list) else []

    async def list_all_rows(self, session: aiohttp.ClientSession) -> list[dict[str, Any]]:
        page_size = min(self.settings.njutable_page_size, 1000)
        base = await self.get_base_token(session)
        all_rows: list[dict[str, Any]] = []
        start = 0
        retry401 = False
        while True:
            try:
                rows = await self.list_rows_page(session, base, start=start, limit=page_size)
            except PermissionError:
                if retry401:
                    raise
                base = await self.get_base_token(session)
                retry401 = True
                continue
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            start += len(rows)
            retry401 = False
        return all_rows
