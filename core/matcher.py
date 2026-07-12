from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.aliases import build_major_index, majors_match_fuzzy
from core.normalize import (
    names_match,
    normalize_notice_no,
    normalize_student_id,
    notice_nos_match,
    student_ids_match,
    student_qq_matches,
)
from core.parser import ParsedApplication
from data_source.students import Student

MatchStrength = Literal["strong", "weak", "none", "auxiliary"]
MatchedBy = Literal[
    "name_studentId",
    "name_noticeNo",
    "name_major",
    "name_academy",
    "qq_aux",
]


@dataclass
class MatchResult:
    strength: MatchStrength
    confidence: float
    reason: str
    matched_student_key: str | None = None
    matched_student: Student | None = None
    matched_by: list[str] = field(default_factory=list)
    qq_match: bool = False


def _academies_match(a: str, b: str) -> bool:
    na = a.strip().replace(" ", "")
    nb = b.strip().replace(" ", "")
    return na == nb or na in nb or nb in na


def has_credential_conflict(parsed: ParsedApplication, student: Student) -> bool:
    if (
        parsed.student_id
        and student.student_id
        and not student_ids_match(parsed.student_id, student.student_id)
    ):
        return True
    if (
        parsed.notice_no
        and student.notice_no
        and not notice_nos_match(parsed.notice_no, student.notice_no)
    ):
        return True
    return False


def _build_result(
    strength: MatchStrength,
    student: Student,
    matched_by: list[str],
    confidence: float,
    reason: str,
    *,
    qq_match: bool = False,
) -> MatchResult:
    return MatchResult(
        strength=strength,
        matched_student_key=student.key,
        matched_student=student,
        matched_by=matched_by,
        confidence=confidence,
        reason=reason,
        qq_match=qq_match,
    )


def _conflict_result() -> MatchResult:
    return MatchResult(
        strength="none",
        confidence=0,
        reason="申请信息存在冲突（学号与通知书编号不一致），需人工复核",
    )


def _notice_candidates(parsed: ParsedApplication) -> list[str]:
    items = list(parsed.notice_no_candidates or [])
    if parsed.notice_no:
        norm = normalize_notice_no(parsed.notice_no)
        if norm and norm not in items:
            items.insert(0, norm)
    return items


def _match_by_notice_candidates(
    parsed: ParsedApplication,
    students: list[Student],
) -> MatchResult | None:
    if not parsed.name:
        return None
    candidates = _notice_candidates(parsed)
    if not candidates:
        return None
    matched_students: list[Student] = []
    for candidate in candidates:
        for student in students:
            if student.notice_no and notice_nos_match(candidate, student.notice_no):
                if names_match(parsed.name, student.name):
                    matched_students.append(student)
    if not matched_students:
        return None
    unique = {s.key: s for s in matched_students}
    if len(unique) > 1:
        return MatchResult(
            strength="none",
            confidence=0,
            reason="多个通知书编号候选匹配不同学生，需人工复核",
        )
    student = next(iter(unique.values()))
    if has_credential_conflict(parsed, student):
        return _conflict_result()
    return _build_result(
        "strong", student, ["name_noticeNo"], 0.95, "姓名+通知书编号强匹配"
    )


def match_student(
    parsed: ParsedApplication,
    students: list[Student],
    applicant_user_id: str | None = None,
) -> MatchResult:
    known_majors = build_major_index(students)

    if parsed.name and parsed.student_id:
        sid_norm = normalize_student_id(parsed.student_id)
        candidates: list[Student] = []
        for student in students:
            if not student.student_id or not names_match(parsed.name, student.name):
                continue
            if student_ids_match(parsed.student_id, student.student_id):
                candidates.append(student)
                continue
            if (
                len(sid_norm) == 8
                and sid_norm.startswith("261")
                and normalize_student_id(student.student_id).startswith(sid_norm)
            ):
                candidates.append(student)
        if len(candidates) == 1:
            student = candidates[0]
            exact_sid = student_ids_match(parsed.student_id, student.student_id)
            if exact_sid and has_credential_conflict(parsed, student):
                return _conflict_result()
            if not exact_sid and (
                parsed.notice_no
                and student.notice_no
                and not notice_nos_match(parsed.notice_no, student.notice_no)
            ):
                return _conflict_result()
            qq_match = student_qq_matches(applicant_user_id, student.qq)
            reason = (
                "姓名+学号强匹配"
                if exact_sid
                else "姓名+学号前缀强匹配（申请学号位数偏短，需人工确认）"
            )
            return _build_result(
                "strong",
                student,
                ["name_studentId"],
                0.9 if reason.startswith("姓名+学号前缀") else 0.95,
                reason,
                qq_match=qq_match,
            )
        if len(candidates) > 1:
            return MatchResult(
                strength="none",
                confidence=0,
                reason="姓名+学号前缀匹配多条记录，需人工复核",
            )

    if parsed.name and parsed.notice_no:
        for student in students:
            if (
                student.notice_no
                and names_match(parsed.name, student.name)
                and notice_nos_match(parsed.notice_no, student.notice_no)
            ):
                if has_credential_conflict(parsed, student):
                    return _conflict_result()
                qq_match = student_qq_matches(applicant_user_id, student.qq)
                return _build_result(
                    "strong",
                    student,
                    ["name_noticeNo"],
                    0.95,
                    "姓名+通知书编号强匹配",
                    qq_match=qq_match,
                )

    notice_candidate_match = _match_by_notice_candidates(parsed, students)
    if notice_candidate_match is not None:
        if notice_candidate_match.strength == "strong" and notice_candidate_match.matched_student:
            notice_candidate_match.qq_match = student_qq_matches(
                applicant_user_id, notice_candidate_match.matched_student.qq
            )
        return notice_candidate_match

    if parsed.name and parsed.major:
        matches = [
            s
            for s in students
            if s.major
            and names_match(parsed.name, s.name)
            and majors_match_fuzzy(parsed.major, s.major, known_majors)
        ]
        if len(matches) == 1:
            qq_match = student_qq_matches(applicant_user_id, matches[0].qq)
            return _build_result(
                "weak",
                matches[0],
                ["name_major"],
                0.6,
                "姓名+专业弱匹配（需人工复核）",
                qq_match=qq_match,
            )
        if len(matches) > 1:
            return MatchResult(
                strength="weak",
                confidence=0.4,
                reason=f"姓名+专业弱匹配但存在 {len(matches)} 条候选，需人工复核",
            )

    if parsed.name and parsed.academy:
        matches = [
            s
            for s in students
            if s.academy
            and names_match(parsed.name, s.name)
            and _academies_match(parsed.academy, s.academy)
        ]
        if len(matches) == 1:
            qq_match = student_qq_matches(applicant_user_id, matches[0].qq)
            return _build_result(
                "weak",
                matches[0],
                ["name_academy"],
                0.55,
                "姓名+书院弱匹配（需人工复核）",
                qq_match=qq_match,
            )
        if len(matches) > 1:
            return MatchResult(
                strength="weak",
                confidence=0.4,
                reason=f"姓名+书院弱匹配但存在 {len(matches)} 条候选，需人工复核",
            )

    if applicant_user_id and parsed.name:
        for student in students:
            if student_qq_matches(applicant_user_id, student.qq) and names_match(
                parsed.name, student.name
            ):
                return _build_result(
                    "auxiliary",
                    student,
                    ["qq_aux"],
                    0.5,
                    "QQ+姓名辅助匹配（需人工复核）",
                    qq_match=True,
                )

    if parsed.student_id:
        by_sid = [
            s
            for s in students
            if s.student_id and student_ids_match(parsed.student_id, s.student_id)
        ]
        if len(by_sid) == 1 and not parsed.name:
            return MatchResult(
                strength="none",
                matched_student_key=by_sid[0].key,
                confidence=0.3,
                reason="仅学号匹配，缺少姓名，需人工复核",
            )

    if parsed.name and not parsed.student_id and not parsed.notice_no and not parsed.major and not parsed.academy:
        by_name = [s for s in students if names_match(parsed.name, s.name)]
        if len(by_name) == 1:
            return MatchResult(
                strength="none",
                matched_student_key=by_name[0].key,
                confidence=0.2,
                reason="仅姓名匹配，信息不足，需人工复核",
            )
        if len(by_name) > 1:
            return MatchResult(
                strength="none",
                confidence=0.1,
                reason=f"仅姓名匹配但存在 {len(by_name)} 条同名记录，需人工复核",
            )

    if (parsed.major or parsed.academy) and not parsed.name:
        return MatchResult(
            strength="none",
            confidence=0.1,
            reason="仅专业/书院信息，无法确认身份，需人工复核",
        )

    return MatchResult(strength="none", confidence=0, reason="未找到匹配记录")


def is_non_grade26(match: MatchResult, parsed: ParsedApplication) -> bool:
    from core.normalize import is_grade26_student_id

    if parsed.student_id and not is_grade26_student_id(parsed.student_id):
        return True
    if match.matched_student and match.matched_student.student_id:
        if not is_grade26_student_id(match.matched_student.student_id):
            return True
    return False
