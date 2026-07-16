from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

from config import PluginSettings, redact_tokens_in_string
from data_source.njutable_client import build_list_rows_url
from data_source.student_cache import SyncState, utc_now_iso
from graduate.cache import GraduateStudentCache
from graduate.models import (
    SENSITIVE_GRAD_FIELD_NAMES,
    GraduateStudent,
    build_graduate_key,
)

# Separate token cache so undergrad/grad API tokens never collide.
_grad_token_cache: dict[str, Any] = {}
TOKEN_TTL_SECONDS = int(2.5 * 24 * 60 * 60)


def clear_grad_base_token_cache() -> None:
    _grad_token_cache.clear()


def _cell_value(row: dict[str, Any], col_name: str) -> str | None:
    value = row.get(col_name)
    if value is None or value == "":
        return None
    text = str(value).strip()
    return text or None


def strip_sensitive_grad_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Drop id-card tail and similar sensitive columns before mapping."""
    cleaned: dict[str, Any] = {}
    for key, value in row.items():
        key_str = str(key)
        if key_str in SENSITIVE_GRAD_FIELD_NAMES:
            continue
        if "证件号码" in key_str or "身份证" in key_str:
            continue
        cleaned[key_str] = value
    return cleaned


def map_row_to_graduate(
    row: dict[str, Any], settings: PluginSettings
) -> GraduateStudent | None:
    safe = strip_sensitive_grad_fields(row)
    name = _cell_value(safe, settings.grad_col_name)
    admission_type = _cell_value(safe, settings.grad_col_admission_type)
    major_name = _cell_value(safe, settings.grad_col_major_name)
    major_code = _cell_value(safe, settings.grad_col_major_code) or ""
    college = _cell_value(safe, settings.grad_col_college) or ""
    if not name or not admission_type or not major_name:
        return None

    # Normalize admission type lightly
    adm = admission_type.strip()
    if adm not in {"硕士", "博士"}:
        # Keep raw but prefer known prefixes
        if "博士" in adm or adm in {"博", "直博"}:
            adm = "博士"
        elif "硕士" in adm or adm in {"硕", "专硕", "学硕"}:
            adm = "硕士"

    source_id = _cell_value(safe, settings.grad_col_id) or ""
    if not source_id:
        source_id = str(safe.get("_id") or safe.get("id") or "")
    student = GraduateStudent(
        source_id=source_id,
        admission_type=adm,
        college=college,
        major_code=major_code,
        major_name=major_name,
        name=name,
        short_code_id=_cell_value(safe, settings.grad_col_short_code_id),
        imported_at=_cell_value(safe, settings.grad_col_imported_at),
    )
    student.key = build_graduate_key(student)
    return student


class GradNjuTableClient:
    def __init__(self, settings: PluginSettings) -> None:
        self.settings = settings

    async def get_base_token(self, session: aiohttp.ClientSession) -> dict[str, str]:
        token_key = self.settings.grad_njutable_api_token
        now = time.time()
        cached = _grad_token_cache.get(token_key)
        if cached and cached["expires_at"] > now:
            return cached["value"]

        url = (
            self.settings.grad_njutable_server_url.rstrip("/")
            + "/api/v2.1/dtable/app-access-token/"
        )
        headers = {
            "Authorization": f"Bearer {self.settings.grad_njutable_api_token}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.settings.grad_njutable_timeout_ms / 1000)
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            body_text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    redact_tokens_in_string(
                        f"Grad Base-Token request failed ({resp.status}): {body_text[:200]}",
                        self.settings,
                    )
                )
            data = await resp.json()
        access_token = str(data.get("access_token", ""))
        dtable_uuid = str(data.get("dtable_uuid", ""))
        if not access_token or not dtable_uuid:
            raise RuntimeError("Grad Base-Token response missing access_token or dtable_uuid")
        value = {"access_token": access_token, "dtable_uuid": dtable_uuid}
        _grad_token_cache[token_key] = {
            "value": value,
            "expires_at": now + TOKEN_TTL_SECONDS,
        }
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
            "table_name": self.settings.grad_njutable_table_name,
            "start": start,
            "limit": limit,
            "convert_keys": "true",
        }
        if self.settings.grad_njutable_view_name:
            params["view_name"] = self.settings.grad_njutable_view_name
        url = build_list_rows_url(
            self.settings.grad_njutable_server_url, base["dtable_uuid"], params
        )
        headers = {
            "Authorization": f"Bearer {base['access_token']}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.settings.grad_njutable_timeout_ms / 1000)
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            if resp.status == 401:
                clear_grad_base_token_cache()
                raise PermissionError("GRAD_ROWS_UNAUTHORIZED")
            body_text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    redact_tokens_in_string(
                        f"Grad list rows failed ({resp.status}): {body_text[:200]}",
                        self.settings,
                    )
                )
            data = await resp.json()
        rows = data.get("rows", [])
        return rows if isinstance(rows, list) else []

    async def list_all_rows(self, session: aiohttp.ClientSession) -> list[dict[str, Any]]:
        page_size = min(self.settings.grad_njutable_page_size, 1000)
        base = await self.get_base_token(session)
        all_rows: list[dict[str, Any]] = []
        start = 0
        while True:
            try:
                page = await self.list_rows_page(
                    session, base, start=start, limit=page_size
                )
            except PermissionError:
                base = await self.get_base_token(session)
                page = await self.list_rows_page(
                    session, base, start=start, limit=page_size
                )
            all_rows.extend(page)
            if len(page) < page_size:
                break
            start += page_size
        return all_rows


async def sync_graduate_students(
    settings: PluginSettings,
    cache: GraduateStudentCache,
    session: aiohttp.ClientSession | None = None,
) -> SyncState:
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    assert session is not None
    try:
        if not settings.grad_njutable_server_url or not settings.grad_njutable_api_token:
            raise RuntimeError("Graduate NJUTable not configured")
        if not settings.grad_njutable_table_name:
            raise RuntimeError("Graduate NJUTable table_name empty")

        client = GradNjuTableClient(settings)
        rows = await client.list_all_rows(session)
        mapped: list[GraduateStudent] = []
        filtered = 0
        for row in rows:
            if not isinstance(row, dict):
                filtered += 1
                continue
            student = map_row_to_graduate(row, settings)
            if student is None:
                filtered += 1
                continue
            mapped.append(student)

        cache.save_students(mapped)
        state = SyncState(
            last_sync_at=utc_now_iso(),
            last_sync_result="success",
            row_count=len(mapped),
            filtered_count=filtered,
            raw_row_count=len(rows),
            mapped_count=len(mapped),
            source="grad_nju_table",
        )
        cache.save_sync_state(state)
        return state
    except Exception as exc:
        # Preserve old cache on failure.
        prev = cache.load_sync_state()
        failed = SyncState(
            last_sync_at=prev.last_sync_at,
            last_sync_result=f"failed:{type(exc).__name__}",
            row_count=prev.row_count,
            filtered_count=prev.filtered_count,
            raw_row_count=prev.raw_row_count,
            mapped_count=prev.mapped_count,
            source="grad_nju_table",
            next_sync_at=prev.next_sync_at,
            last_sync_source=prev.last_sync_source,
        )
        cache.save_sync_state(failed)
        raise
    finally:
        if own_session:
            await session.close()


def load_graduates_for_audit(
    settings: PluginSettings, cache: GraduateStudentCache
) -> list[GraduateStudent]:
    return cache.load_students()
