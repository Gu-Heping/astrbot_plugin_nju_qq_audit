"""Admin lookup UX for undergraduate exam_no (fictional credentials)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.formatter import format_help
from admin.lookup import (
    format_lookup_help,
    format_lookup_result,
    parse_lookup_args,
    run_lookup,
)
from admin.release import format_catchup_help, format_release_help
from config import load_settings
from core.normalize import mask_exam_no
from data_source.student_cache import StudentCache
from data_source.students import Student

FICTIONAL_EXAM = "26123456000001"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _cache_with(tmp_path: Path, students: list[Student]):
    settings = load_settings(
        DummyConfig({"student_source": "mock", "target_group_ids": "1"})
    )
    cache = StudentCache(tmp_path)
    cache.save_students(students)
    return settings, cache


def test_parse_lookup_exam_no_not_student_id():
    parsed = parse_lookup_args(f"张三 {FICTIONAL_EXAM}")
    assert parsed.name == "张三"
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.student_id is None


def test_parse_lookup_plus_exam_major():
    parsed = parse_lookup_args(f"张三+{FICTIONAL_EXAM}+计算机科学与技术")
    assert parsed.name == "张三"
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.major == "计算机科学与技术"
    assert parsed.student_id is None


def test_run_lookup_name_exam_no_strong(tmp_path):
    settings, cache = _cache_with(
        tmp_path,
        [
            Student(
                name="张三",
                student_id="261880001",
                exam_no=FICTIONAL_EXAM,
                major="计算机科学与技术",
                updated_at="t",
                status="已确认",
            )
        ],
    )
    query = parse_lookup_args(f"张三 {FICTIONAL_EXAM}")
    result = run_lookup(settings, cache, query=query)
    assert result.match.strength == "strong"
    assert result.match.reason == "姓名+考生号强匹配"
    assert "name_examNo" in result.match.matched_by


def test_format_lookup_result_masks_exam_no(tmp_path):
    settings, cache = _cache_with(
        tmp_path,
        [
            Student(
                name="张三",
                student_id="261880001",
                exam_no=FICTIONAL_EXAM,
                major="计算机科学与技术",
                updated_at="t",
            )
        ],
    )
    result = run_lookup(
        settings,
        cache,
        name="张三",
        exam_no=FICTIONAL_EXAM,
    )
    text = format_lookup_result(result)
    assert "查询考生号：" in text
    assert mask_exam_no(FICTIONAL_EXAM) in text
    assert FICTIONAL_EXAM not in text.split("用法：")[0]


def test_format_lookup_help_mentions_exam_no():
    help_text = format_lookup_help()
    assert "考生号" in help_text
    assert FICTIONAL_EXAM in help_text


def test_debug_advanced_help_mentions_credentials():
    debug = format_help(topic="debug")
    advanced = format_help(topic="advanced")
    assert "学号/通知书/考生号" in debug
    assert "学号/通知书/考生号" in advanced


def test_release_catchup_copy_mentions_exam_no_grade():
    settings = load_settings(DummyConfig({"batch_approve_max_count": 20}))
    release = format_release_help(3, settings)
    catchup = format_catchup_help(settings)
    batch = format_help(topic="batch")
    assert "学号/考生号判断为 26 级" in release
    assert "学号/考生号判断为 26 级" in catchup
    assert "学号/考生号判断为 26 级" in batch


def test_legacy_lookup_student_id_still_works():
    parsed = parse_lookup_args("张三 261220001")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.exam_no is None


def test_legacy_lookup_student_id_with_major():
    parsed = parse_lookup_args("张三 261220001 计算机科学与技术")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.major == "计算机科学与技术"
    assert parsed.exam_no is None
