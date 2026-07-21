"""NJUTable exam_no sync / cache sanitize (fictional data)."""

from __future__ import annotations

from config import PluginSettings, load_settings
from data_source.njutable_provider import map_row_to_student
from data_source.students import Student, build_student_key, sanitize_student_for_cache


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


FICTIONAL_EXAM = "26123456000001"


def test_map_row_reads_exam_no_column():
    settings = PluginSettings()
    row = {
        "姓名": "张三",
        "考生号": f" {FICTIONAL_EXAM} ",
        "学号": "261880001",
        "专业": "计算机科学与技术",
        "身份证号": "110101199001011234",
        "_id": "1",
    }
    student = map_row_to_student(row, settings)
    assert student is not None
    assert student.exam_no == FICTIONAL_EXAM
    assert not hasattr(student, "id_card") or getattr(student, "id_card", None) is None
    assert "身份证" not in student.to_dict()
    assert "110101199001011234" not in str(student.to_dict())


def test_sanitize_student_for_cache_keeps_exam_no():
    student = Student(
        name="张三",
        updated_at="t",
        student_id="261880001",
        exam_no=FICTIONAL_EXAM,
        major="计算机科学与技术",
    )
    cleaned = sanitize_student_for_cache(student)
    assert cleaned.exam_no == FICTIONAL_EXAM


def test_build_student_key_falls_back_to_exam_no():
    student = Student(
        name="张三",
        updated_at="t",
        exam_no=FICTIONAL_EXAM,
        major="计算机科学与技术",
    )
    assert build_student_key(student) == f"{FICTIONAL_EXAM}:张三"
    assert build_student_key(
        {"name": "张三", "exam_no": FICTIONAL_EXAM}
    ) == f"{FICTIONAL_EXAM}:张三"
    assert build_student_key({"exam_no": FICTIONAL_EXAM}) == FICTIONAL_EXAM


def test_load_settings_supports_custom_exam_no_column():
    settings = load_settings(
        DummyConfig({"njutable_col_exam_no": "高考考生号"})
    )
    assert settings.njutable_cols.exam_no == "高考考生号"
    row = {
        "姓名": "张三",
        "高考考生号": FICTIONAL_EXAM,
        "_id": "2",
    }
    student = map_row_to_student(row, settings)
    assert student is not None
    assert student.exam_no == FICTIONAL_EXAM
