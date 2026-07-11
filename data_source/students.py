from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Decision = Literal["approve", "manual_review", "reject", "ignored"]
PendingStatus = Literal["pending", "processed", "failed", "ignored", "external"]
MatchStrength = Literal["strong", "weak", "none", "auxiliary"]

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "身份证号",
        "收件人",
        "家庭地址",
        "邮政编码",
        "联系电话",
        "联系手机",
        "修改者",
        "身高",
        "体重",
    }
)

STUDENT_DISPLAY_FIELDS = (
    "name",
    "student_id",
    "notice_no",
    "major",
    "academy",
    "status",
    "exam_no",
    "batch",
    "subject",
    "origin",
    "gender",
)


@dataclass
class Student:
    name: str
    updated_at: str
    key: str = ""
    notice_no: str | None = None
    exam_no: str | None = None
    gender: str | None = None
    origin: str | None = None
    subject: str | None = None
    batch: str | None = None
    major: str | None = None
    score: str | float | None = None
    middle_school: str | None = None
    student_id: str | None = None
    academy: str | None = None
    status: str | None = None
    qq: str | None = None
    source_row_id: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            self.key = build_student_key(self)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Student:
        return cls(
            name=str(data["name"]),
            updated_at=str(data.get("updated_at", "")),
            key=str(data.get("key", "")),
            notice_no=data.get("notice_no"),
            exam_no=data.get("exam_no"),
            gender=data.get("gender"),
            origin=data.get("origin"),
            subject=data.get("subject"),
            batch=data.get("batch"),
            major=data.get("major"),
            score=data.get("score"),
            middle_school=data.get("middle_school"),
            student_id=data.get("student_id"),
            academy=data.get("academy"),
            status=data.get("status"),
            qq=data.get("qq"),
            source_row_id=data.get("source_row_id"),
        )


@dataclass
class ActionResult:
    ok: bool
    retcode: int | None = None
    message: str | None = None
    data: dict[str, Any] | None = None


@dataclass
class PendingRequest:
    id: str
    group_id: str
    user_id: str
    comment: str
    flag: str
    sub_type: str
    decision: Decision
    confidence: float
    reason: str
    mode: str
    status: PendingStatus
    created_at: str
    match_strength: MatchStrength = "none"
    parsed: dict[str, Any] = field(default_factory=dict)
    match: dict[str, Any] = field(default_factory=dict)
    processed_at: str | None = None
    action_result: ActionResult | None = None
    admin_override: bool = False
    admin_user_id: str | None = None
    admin_command: str | None = None
    matched_student_key: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "comment": self.comment,
            "sub_type": self.sub_type,
            "decision": self.decision,
            "confidence": self.confidence,
            "reason": self.reason,
            "mode": self.mode,
            "status": self.status,
            "created_at": self.created_at,
            "processed_at": self.processed_at,
            "match_strength": self.match_strength,
            "parsed": self.parsed,
            "match": {
                k: v
                for k, v in self.match.items()
                if k not in {"flag"}
            },
            "admin_override": self.admin_override,
            "matched_student_key": self.matched_student_key,
        }


def build_student_key(student: Student | dict[str, Any]) -> str:
    if isinstance(student, Student):
        sid = student.student_id
        notice_no = student.notice_no
        name = student.name
    else:
        sid = student.get("student_id")
        notice_no = student.get("notice_no")
        name = student.get("name", "")
    if sid:
        return str(sid)
    if notice_no and name:
        return f"{notice_no}:{name}"
    return str(name)


def sanitize_student_for_cache(student: Student) -> Student:
    allowed_fields = {
        "key",
        "name",
        "updated_at",
        "notice_no",
        "exam_no",
        "gender",
        "origin",
        "subject",
        "batch",
        "major",
        "score",
        "middle_school",
        "student_id",
        "academy",
        "status",
        "qq",
        "source_row_id",
    }
    data = student.to_dict()
    return Student.from_dict({k: v for k, v in data.items() if k in allowed_fields})
