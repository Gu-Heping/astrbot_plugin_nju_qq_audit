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
    parsed = parse_lookup_args("张三 261220001")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.exam_no is None
    parsed = parse_lookup_args("张三 261220001 计算机科学与技术")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.major == "计算机科学与技术"


def test_lookup_strong_hit(tmp_path):
    settings, cache = _cache_with(
        tmp_path,
        [
            Student(
                name="张三",
                student_id="261220001",
                major="计算机科学与技术",
                updated_at="t",
                status="已确认",
            )
        ],
    )
    result = run_lookup(settings, cache, name="张三", student_id="261220001")
    assert result.match.strength == "strong"
    text = format_lookup_result(result)
    assert "匹配强度：strong" in text
    assert "张三" in text
    assert "261220001" in text


def test_lookup_partial_name_only(tmp_path):
    settings, cache = _cache_with(
        tmp_path,
        [
            Student(
                name="张三",
                student_id="261220009",
                major="计算机科学与技术",
                updated_at="t",
            )
        ],
    )
    result = run_lookup(settings, cache, name="张三", student_id="261220001")
    assert result.match.strength != "strong"
    assert len(result.name_hits) == 1
    text = format_lookup_result(result)
    assert "同姓名" in text or "部分匹配" in text


def test_lookup_no_hits(tmp_path):
    settings, cache = _cache_with(tmp_path, [])
    result = run_lookup(settings, cache, name="不存在", student_id="261299999")
    assert result.match.strength == "none"
    assert "无同名" in format_lookup_result(result)
