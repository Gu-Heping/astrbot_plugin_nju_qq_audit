from __future__ import annotations

from typing import Any

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)

from storage.group_display_cache import GroupDisplayCache


def _extract_group_map(data: Any) -> dict[str, str]:
    """Parse OneBot get_group_list payload into {group_id: group_name}."""
    rows: list[Any]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("data", "group_list", "groups"):
            maybe = data.get(key)
            if isinstance(maybe, list):
                rows = maybe
                break
        else:
            rows = []
    else:
        rows = []

    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        gid = row.get("group_id")
        if gid is None:
            gid = row.get("groupId")
        name = row.get("group_name") or row.get("groupName") or row.get("name")
        if gid is None or not name:
            continue
        result[str(gid).strip()] = str(name).strip()
    return result


class DisplayContext:
    """Best-effort human labels for admin-facing messages.

    Failures never raise to callers; always return a safe fallback string.
    """

    def __init__(
        self,
        actions: Any,
        cache: GroupDisplayCache,
    ) -> None:
        self.actions = actions
        self.cache = cache

    def set_actions(self, actions: Any) -> None:
        self.actions = actions

    async def get_group_label(self, group_id: str) -> str:
        gid = str(group_id or "").strip()
        if not gid:
            return "群 （未知）"
        try:
            name = None
            if not self.cache.is_expired():
                name = self.cache.get_name(gid)
            if not name:
                await self._refresh_group_cache()
                name = self.cache.get_name(gid)
            if name:
                return f"{name}（{gid}）"
        except Exception:
            logger.debug(
                "[audit] get_group_label failed group=%s", gid, exc_info=True
            )
        return f"群 {gid}"

    async def get_user_label(
        self,
        group_id: str,
        user_id: str,
        parsed: dict | None = None,
    ) -> str:
        uid = str(user_id or "").strip()
        if not uid:
            return "未知用户"
        try:
            name = None
            if isinstance(parsed, dict):
                raw = parsed.get("name")
                if isinstance(raw, str) and raw.strip():
                    name = raw.strip()
            if name:
                return f"{name}（{uid}）"
        except Exception:
            logger.debug(
                "[audit] get_user_label failed user=%s", uid, exc_info=True
            )
        return uid

    async def _refresh_group_cache(self) -> None:
        if self.actions is None:
            return
        try:
            result = await self.actions.get_group_list()
        except Exception:
            logger.debug("[audit] get_group_list raised", exc_info=True)
            return
        if not getattr(result, "ok", False):
            return
        mapping = _extract_group_map(getattr(result, "data", None))
        if mapping:
            await self.cache.replace_groups(mapping)
