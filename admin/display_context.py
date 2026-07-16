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
        name = _group_name_from_row(row)
        gid = _group_id_from_row(row)
        if gid is None or not name:
            continue
        result[gid] = name
    return result


def _group_id_from_row(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    gid = row.get("group_id")
    if gid is None:
        gid = row.get("groupId")
    if gid is None:
        return None
    return str(gid).strip() or None


def _group_name_from_row(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    name = row.get("group_name") or row.get("groupName") or row.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _nickname_from_stranger(data: Any) -> str | None:
    """Extract QQ nickname from get_stranger_info payload. Never use application name."""
    row: Any = data
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict):
            row = nested
    if not isinstance(row, dict):
        return None
    for key in ("nickname", "nick", "user_displayname", "card"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class DisplayContext:
    """Best-effort human labels for admin-facing messages.

    Failures never raise to callers; always return a safe fallback string.
    Application identity (parsed.name) must never be shown as QQ nickname.
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
            if not name:
                name = await self._fetch_single_group_name(gid)
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
        # parsed is intentionally unused for nickname — applicant name ≠ QQ nick.
        del parsed
        del group_id
        uid = str(user_id or "").strip()
        if not uid:
            return "未知用户"
        try:
            nickname = await self._fetch_stranger_nickname(uid)
            if nickname:
                return f"{nickname}（{uid}）"
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

    async def _fetch_single_group_name(self, group_id: str) -> str | None:
        if self.actions is None or not hasattr(self.actions, "get_group_info"):
            return None
        try:
            result = await self.actions.get_group_info(group_id)
        except Exception:
            logger.debug("[audit] get_group_info raised group=%s", group_id, exc_info=True)
            return None
        if not getattr(result, "ok", False):
            return None
        data = getattr(result, "data", None)
        row = data
        if isinstance(data, dict) and "group_name" not in data and isinstance(data.get("data"), dict):
            row = data.get("data")
        name = _group_name_from_row(row)
        if name:
            try:
                await self.cache.upsert_group(group_id, name)
            except Exception:
                logger.debug("[audit] cache single group name failed", exc_info=True)
        return name

    async def _fetch_stranger_nickname(self, user_id: str) -> str | None:
        if self.actions is None or not hasattr(self.actions, "get_stranger_info"):
            return None
        try:
            result = await self.actions.get_stranger_info(user_id)
        except Exception:
            logger.debug(
                "[audit] get_stranger_info raised user=%s", user_id, exc_info=True
            )
            return None
        if not getattr(result, "ok", False):
            return None
        return _nickname_from_stranger(getattr(result, "data", None))
