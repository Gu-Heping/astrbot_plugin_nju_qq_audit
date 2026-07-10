from __future__ import annotations

from typing import Any

import aiohttp

from config import PluginSettings
from data_source.mock_provider import generate_mock_students
from data_source.njutable_client import NjuTableClient
from data_source.student_cache import StudentCache, SyncState, utc_now_iso
from data_source.students import Student, build_student_key, sanitize_student_for_cache
from core.normalize import parse_qq_field

HARD_EXCLUDED_STATUSES = frozenset({"有问题"})


def _parse_qq_cell(value: str | None) -> str | None:
    if not value:
        return None
    return parse_qq_field(value)


def _cell_value(row: dict[str, Any], col_name: str) -> str | None:
    value = row.get(col_name)
    if value is None or value == "":
        return None
    text = str(value).strip()
    return text or None


def _cell_number_or_string(row: dict[str, Any], col_name: str) -> str | float | None:
    value = row.get(col_name)
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    return text or None


def map_row_to_student(row: dict[str, Any], settings: PluginSettings) -> Student | None:
    cols = settings.njutable_cols
    name = _cell_value(row, cols.name)
    if not name:
        return None
    student = Student(
        name=name,
        updated_at=utc_now_iso(),
        notice_no=_cell_value(row, cols.notice_no),
        exam_no=_cell_value(row, cols.exam_no),
        gender=_cell_value(row, cols.gender),
        origin=_cell_value(row, cols.origin),
        subject=_cell_value(row, cols.subject),
        batch=_cell_value(row, cols.batch),
        major=_cell_value(row, cols.major),
        score=_cell_number_or_string(row, cols.score),
        middle_school=_cell_value(row, cols.middle_school),
        student_id=_cell_value(row, cols.student_id),
        academy=_cell_value(row, cols.academy),
        status=_cell_value(row, cols.status),
        qq=_parse_qq_cell(_cell_value(row, cols.qq)),
        source_row_id=str(row.get("_id", "")) or None,
    )
    student.key = build_student_key(student)
    return student


def is_status_allowed(status: str | None, allowed_statuses: tuple[str, ...]) -> bool:
    if not status or not status.strip():
        return False
    if status in HARD_EXCLUDED_STATUSES:
        return False
    return status in allowed_statuses


def filter_students_by_status(
    students: list[Student], allowed_statuses: tuple[str, ...]
) -> list[Student]:
    return [s for s in students if is_status_allowed(s.status, allowed_statuses)]


def filter_students_for_sync(
    students: list[Student],
    settings: PluginSettings,
) -> list[Student]:
    if settings.njutable_ignore_status_filter:
        return [s for s in students if s.status not in HARD_EXCLUDED_STATUSES]
    return filter_students_by_status(students, settings.njutable_allowed_statuses)


async def sync_students(
    settings: PluginSettings,
    cache: StudentCache,
    session: aiohttp.ClientSession | None = None,
) -> SyncState:
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    assert session is not None
    try:
        if settings.student_source == "mock":
            filtered = generate_mock_students()
            cache.save_students(filtered)
            state = SyncState(
                last_sync_at=utc_now_iso(),
                last_sync_result="success",
                row_count=len(filtered),
                filtered_count=len(filtered),
                source="mock",
            )
            cache.save_sync_state(state)
            return state

        if not settings.njutable_server_url or not settings.njutable_api_token:
            raise RuntimeError("NJUTable not configured")

        client = NjuTableClient(settings)
        rows = await client.list_all_rows(session)
        mapped = [
            student
            for row in rows
            if (student := map_row_to_student(row, settings)) is not None
        ]
        filtered = [
            sanitize_student_for_cache(s)
            for s in filter_students_for_sync(mapped, settings)
        ]
        cache.save_students(filtered)
        state = SyncState(
            last_sync_at=utc_now_iso(),
            last_sync_result="success",
            row_count=len(mapped),
            filtered_count=len(filtered),
            source="nju_table",
        )
        cache.save_sync_state(state)
        return state
    except Exception as exc:
        state = cache.load_sync_state()
        state.last_sync_at = utc_now_iso()
        state.last_sync_result = f"failed: {type(exc).__name__}"
        cache.save_sync_state(state)
        raise
    finally:
        if own_session:
            await session.close()


def load_students_for_audit(settings: PluginSettings, cache: StudentCache) -> list[Student]:
    if settings.student_source == "mock":
        cached = cache.load_students()
        return cached or generate_mock_students()
    cached = cache.load_students()
    return cached
