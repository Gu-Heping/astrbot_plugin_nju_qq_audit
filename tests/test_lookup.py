from pathlib import Path

from admin.lookup import (
    format_lookup_result,
    parse_lookup_args,
    run_lookup,
)
from config import load_settings
from data_source.student_cache import StudentCache
from data_source.students import Student


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _cache_with(tmp_path: Path, students: list[Student]) -> tuple:
    settings = load_settings(
        DummyConfig({"student_source": "mock", "target_group_ids": "1"})
    )
    cache = StudentCache(tmp_path)
    cache.save_students(students)
    return settings, cache


def test_parse_lookup_args():
    assert parse_lookup_args("马至成 261200008") == ("马至成", "261200008", None)
    name, sid, major = parse_lookup_args("马至成 261200008 环境科学与工程类")
    assert name == "马至成"
    assert sid == "261200008"
    assert major == "环境科学与工程类"


def test_lookup_strong_hit(tmp_path):
    settings, cache = _cache_with(
        tmp_path,
        [
            Student(
                name="马至成",
                student_id="261200008",
                major="环境科学与工程类",
                updated_at="t",
                status="已确认",
            )
        ],
    )
    result = run_lookup(settings, cache, name="马至成", student_id="261200008")
    assert result.match.strength == "strong"
    text = format_lookup_result(result)
    assert "匹配强度：strong" in text
    assert "马至成" in text
    assert "261200008" in text


def test_lookup_partial_name_only(tmp_path):
    settings, cache = _cache_with(
        tmp_path,
        [
            Student(
                name="马至成",
                student_id="261200009",
                major="环境科学与工程类",
                updated_at="t",
            )
        ],
    )
    result = run_lookup(settings, cache, name="马至成", student_id="261200008")
    assert result.match.strength != "strong"
    assert len(result.name_hits) == 1
    text = format_lookup_result(result)
    assert "同姓名" in text or "部分匹配" in text


def test_lookup_no_hits(tmp_path):
    settings, cache = _cache_with(tmp_path, [])
    result = run_lookup(settings, cache, name="不存在", student_id="261299999")
    assert result.match.strength == "none"
    assert "无同名或同学号" in format_lookup_result(result)
