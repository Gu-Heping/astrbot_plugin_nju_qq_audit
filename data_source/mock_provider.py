from __future__ import annotations

from data_source.students import Student, build_student_key, sanitize_student_for_cache

MAJOR_MAPPING = [
    {"institution_code": "122", "college": "计算机学院", "admission_major": "计算机科学与技术"},
    {"institution_code": "118", "college": "电子科学与工程学院", "admission_major": "电子信息类"},
    {"institution_code": "120", "college": "软件学院", "admission_major": "软件工程"},
    {"institution_code": "121", "college": "人工智能学院", "admission_major": "人工智能"},
    {"institution_code": "117", "college": "自动化学院", "admission_major": "工科试验班"},
    {"institution_code": "125", "college": "匡亚明学院", "admission_major": "理科试验班"},
    {"institution_code": "119", "college": "集成电路学院", "admission_major": "集成电路设计与集成系统"},
    {"institution_code": "123", "college": "建筑与规划学院", "admission_major": "建筑学"},
    {"institution_code": "124", "college": "建筑与规划学院", "admission_major": "城乡规划"},
    {"institution_code": "126", "college": "商学院", "admission_major": "经济管理试验班"},
    {"institution_code": "127", "college": "商学院", "admission_major": "经济管理试验班"},
    {"institution_code": "128", "college": "外国语学院", "admission_major": "英语"},
    {"institution_code": "129", "college": "信息管理学院", "admission_major": "信息管理与信息系统"},
]


def format_student_id26(institution_code: str, sequence: int) -> str:
    return f"261{institution_code}{sequence:03d}"


def format_student_id25(institution_code: str, sequence: int) -> str:
    return f"251{institution_code}{sequence:03d}"


def format_notice_no26(sequence: int) -> str:
    return f"2026{sequence:04d}"


def format_notice_no25(sequence: int) -> str:
    return f"2025{sequence:04d}"


def _build_student(spec: dict, grade: int) -> Student:
    mapping = MAJOR_MAPPING[spec["mapping_index"]]
    if grade == 26:
        student_id = format_student_id26(mapping["institution_code"], spec["sequence"])
        notice_no = format_notice_no26(spec["sequence"])
    else:
        student_id = format_student_id25(mapping["institution_code"], spec["sequence"])
        notice_no = format_notice_no25(spec["sequence"])
    now = "2026-01-01T00:00:00+00:00"
    student = Student(
        name=spec["name"],
        updated_at=now,
        notice_no=notice_no,
        status="对外公布",
        student_id=student_id,
        academy=mapping["college"],
        major=mapping["admission_major"],
        qq=spec.get("qq"),
        exam_no=spec.get("exam_no"),
        batch=spec.get("batch"),
        subject=spec.get("subject"),
        origin=spec.get("origin"),
    )
    student.key = build_student_key(student)
    return sanitize_student_for_cache(student)


MOCK_SPECS = [
    {"name": "张三", "mapping_index": 0, "sequence": 1, "qq": "100001"},
    {"name": "李四", "mapping_index": 1, "sequence": 2},
    {"name": "王五", "mapping_index": 4, "sequence": 3, "qq": "100003"},
    {"name": "赵六", "mapping_index": 3, "sequence": 4},
    {"name": "钱七", "mapping_index": 2, "sequence": 5},
    {"name": "孙八", "mapping_index": 6, "sequence": 6},
    {"name": "周九", "mapping_index": 7, "sequence": 7},
    {"name": "吴十", "mapping_index": 9, "sequence": 8},
    {"name": "郑十一", "mapping_index": 10, "sequence": 9},
    {"name": "陈十二", "mapping_index": 11, "sequence": 10},
    {"name": "林十三", "mapping_index": 0, "sequence": 11},
    {"name": "黄十四", "mapping_index": 3, "sequence": 12, "qq": "100012"},
    {"name": "何十五", "mapping_index": 4, "sequence": 13},
    {"name": "马十六", "mapping_index": 9, "sequence": 14},
    {
        "name": "罗十七",
        "mapping_index": 12,
        "sequence": 15,
        "exam_no": "JS202600015",
        "batch": "本科一批",
        "subject": "理工",
        "origin": "江苏",
    },
]

MOCK_25_SPECS = [
    {"name": "刘学长", "mapping_index": 5, "sequence": 69, "qq": "200069"},
    {"name": "杨学姐", "mapping_index": 0, "sequence": 70},
]


def generate_mock_students() -> list[Student]:
    students = [_build_student(spec, 26) for spec in MOCK_SPECS]
    students.extend(_build_student(spec, 25) for spec in MOCK_25_SPECS)
    return students
