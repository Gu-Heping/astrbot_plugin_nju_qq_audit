from __future__ import annotations

import re

NON_GRADE26_KEYWORDS = [
    "学长",
    "学姐",
    "住届",
    "往届",
    "25级",
    "24级",
    "23级",
    "22级",
    "21级",
    "20级",
    "研究生",
    "硕士",
    "博士",
    "家长",
    "非新生",
]


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(name: str) -> str:
    return normalize_whitespace(name).replace(" ", "")


def normalize_student_id(student_id: str) -> str:
    return re.sub(r"\D", "", student_id)


def get_grade_from_student_id(student_id: str) -> str | None:
    normalized = normalize_student_id(student_id)
    if len(normalized) < 2:
        return None
    return normalized[:2]


def is_grade26_student_id(student_id: str) -> bool:
    return get_grade_from_student_id(student_id) == "26"


def normalize_notice_no(notice_no: str) -> str:
    return notice_no.replace(" ", "").upper()


def normalize_major(major: str) -> str:
    value = normalize_whitespace(major)
    value = re.sub(r"类$", "", value)
    value = re.sub(r"专业$", "", value)
    return value.strip()


def has_non_grade26_keyword(text: str) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower or kw in text for kw in NON_GRADE26_KEYWORDS)


def names_match(a: str, b: str) -> bool:
    return normalize_name(a) == normalize_name(b)


def student_ids_match(a: str, b: str) -> bool:
    return normalize_student_id(a) == normalize_student_id(b)


def notice_nos_match(a: str, b: str) -> bool:
    return normalize_notice_no(a) == normalize_notice_no(b)
