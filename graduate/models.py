from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SENSITIVE_GRAD_FIELD_NAMES = frozenset(
    {
        "证件号码末三位",
        "证件号码",
        "身份证号",
        "身份证",
    }
)


@dataclass
class GraduateStudent:
    source_id: str
    admission_type: str  # 硕士 / 博士
    college: str
    major_code: str
    major_name: str
    name: str
    short_code_id: str | None = None
    imported_at: str | None = None
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "admission_type": self.admission_type,
            "college": self.college,
            "major_code": self.major_code,
            "major_name": self.major_name,
            "name": self.name,
            "short_code_id": self.short_code_id,
            "imported_at": self.imported_at,
            "key": self.key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraduateStudent:
        # Never load id-card tail fields even if present in old/corrupt cache.
        cleaned = {
            k: v
            for k, v in data.items()
            if k not in SENSITIVE_GRAD_FIELD_NAMES
            and "证件" not in str(k)
            and "身份证" not in str(k)
        }
        student = cls(
            source_id=str(cleaned.get("source_id") or ""),
            admission_type=str(cleaned.get("admission_type") or ""),
            college=str(cleaned.get("college") or ""),
            major_code=str(cleaned.get("major_code") or ""),
            major_name=str(cleaned.get("major_name") or ""),
            name=str(cleaned.get("name") or ""),
            short_code_id=(
                str(cleaned["short_code_id"]) if cleaned.get("short_code_id") else None
            ),
            imported_at=(
                str(cleaned["imported_at"]) if cleaned.get("imported_at") else None
            ),
            key=str(cleaned.get("key") or ""),
        )
        if not student.key:
            student.key = build_graduate_key(student)
        return student


def build_graduate_key(student: GraduateStudent | dict[str, Any]) -> str:
    if isinstance(student, GraduateStudent):
        if student.source_id:
            return student.source_id
        return (
            f"{student.name}:{student.admission_type}:"
            f"{student.major_code}:{student.major_name}"
        )
    source_id = str(student.get("source_id") or "").strip()
    if source_id:
        return source_id
    return (
        f"{student.get('name', '')}:{student.get('admission_type', '')}:"
        f"{student.get('major_code', '')}:{student.get('major_name', '')}"
    )


@dataclass
class GraduateParsedApplication:
    raw: str
    name: str | None = None
    major_text: str | None = None
    admission_type: str | None = None  # 硕士 / 博士 / None
    admission_type_raw: str | None = None
    major_code_candidates: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "major_text": self.major_text,
            "major": self.major_text,
            "admission_type": self.admission_type,
            "admission_type_raw": self.admission_type_raw,
            "major_code_candidates": list(self.major_code_candidates),
            "student_id": None,
            "notice_no": None,
            "academy": None,
        }
