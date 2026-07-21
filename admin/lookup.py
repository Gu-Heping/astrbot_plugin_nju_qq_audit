"""Manual student-table lookup for admin diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from core.decision import make_decision
from core.matcher import MatchResult, match_student
from core.normalize import (
    exam_nos_match,
    mask_exam_no,
    names_match,
    normalize_exam_no,
    normalize_name,
    normalize_notice_no,
    normalize_student_id,
    notice_nos_match,
    student_ids_match,
)
from core.parser import ParsedApplication, parse_application_comment
from data_source.njutable_provider import load_students_for_audit
from data_source.students import Student


@dataclass
class LookupResult:
    name: str | None
    student_id: str | None
    exam_no: str | None
    major: str | None
    notice_no: str | None
    academy: str | None
    cache_count: int
    match: MatchResult
    name_hits: list[Student]
    sid_hits: list[Student]
    exam_hits: list[Student]
    notice_hits: list[Student]


def _sid_hits(students: list[Student], student_id: str | None) -> list[Student]:
    if not student_id:
        return []
    return [
        s
        for s in students
        if s.student_id and student_ids_match(student_id, s.student_id)
    ]


def _exam_hits(students: list[Student], exam_no: str | None) -> list[Student]:
    if not exam_no:
        return []
    return [
        s
        for s in students
        if s.exam_no and exam_nos_match(exam_no, s.exam_no)
    ]


def _notice_hits(students: list[Student], notice_no: str | None) -> list[Student]:
    if not notice_no:
        return []
    return [
        s
        for s in students
        if s.notice_no and notice_nos_match(notice_no, s.notice_no)
    ]


def _name_hits(students: list[Student], name: str | None, *, limit: int = 5) -> list[Student]:
    if not name:
        return []
    hits = [s for s in students if names_match(name, s.name)]
    return hits[:limit]


def _normalize_query(parsed: ParsedApplication) -> ParsedApplication:
    name = normalize_name(parsed.name) if parsed.name else None
    student_id = normalize_student_id(parsed.student_id) if parsed.student_id else None
    exam_no = normalize_exam_no(parsed.exam_no) if parsed.exam_no else None
    notice_no = normalize_notice_no(parsed.notice_no) if parsed.notice_no else None
    major = (parsed.major or "").strip() or None
    academy = (parsed.academy or "").strip() or None
    return ParsedApplication(
        raw=parsed.raw or "",
        name=name or None,
        student_id=student_id or None,
        exam_no=exam_no or None,
        notice_no=notice_no or None,
        major=major,
        academy=academy,
        notice_no_candidates=list(parsed.notice_no_candidates or []),
        parse_errors=list(parsed.parse_errors or []),
    )


def lookup_query_has_fields(parsed: ParsedApplication) -> bool:
    return bool(
        parsed.name
        or parsed.student_id
        or parsed.exam_no
        or parsed.notice_no
        or parsed.major
        or parsed.academy
    )


def parse_lookup_args(args: str) -> ParsedApplication:
    """Parse lookup text into ParsedApplication (reuses undergrad parser)."""
    text = (args or "").strip()
    if not text:
        return ParsedApplication(raw="")
    return _normalize_query(parse_application_comment(text))


def run_lookup(
    settings,
    cache,
    *,
    query: ParsedApplication | None = None,
    name: str | None = None,
    student_id: str | None = None,
    exam_no: str | None = None,
    major: str | None = None,
    notice_no: str | None = None,
    academy: str | None = None,
) -> LookupResult:
    if query is not None:
        parsed = _normalize_query(query)
    else:
        parsed = _normalize_query(
            ParsedApplication(
                raw="",
                name=name,
                student_id=student_id,
                exam_no=exam_no,
                major=major,
                notice_no=notice_no,
                academy=academy,
            )
        )
    students = load_students_for_audit(settings, cache)
    match = match_student(parsed, students)
    return LookupResult(
        name=parsed.name,
        student_id=parsed.student_id,
        exam_no=parsed.exam_no,
        major=parsed.major,
        notice_no=parsed.notice_no,
        academy=parsed.academy,
        cache_count=len(students),
        match=match,
        name_hits=_name_hits(students, parsed.name),
        sid_hits=_sid_hits(students, parsed.student_id),
        exam_hits=_exam_hits(students, parsed.exam_no),
        notice_hits=_notice_hits(students, parsed.notice_no),
    )


def format_lookup_result(result: LookupResult) -> str:
    decision = make_decision(
        ParsedApplication(
            raw="",
            name=result.name,
            student_id=result.student_id,
            exam_no=result.exam_no,
            major=result.major,
            notice_no=result.notice_no,
            academy=result.academy,
        ),
        result.match,
        is_target_group=True,
    )
    query_exam = mask_exam_no(result.exam_no) if result.exam_no else "（无）"
    lines = [
        "校对表查询",
        "",
        f"缓存人数：{result.cache_count}",
        f"查询姓名：{result.name or '（无）'}",
        f"查询学号：{result.student_id or '（无）'}",
        f"查询考生号：{query_exam}",
        f"查询通知书：{result.notice_no or '（无）'}",
        f"查询专业：{result.major or '（无）'}",
        "",
        f"匹配强度：{result.match.strength}",
        f"原因：{result.match.reason}",
        f"决策建议：{decision.decision}",
    ]
    if result.match.matched_student is not None:
        s = result.match.matched_student
        exam_display = mask_exam_no(s.exam_no) if s.exam_no else "（无）"
        lines.extend(
            [
                "",
                "命中记录：",
                f"- 姓名：{s.name}",
                f"- 学号：{s.student_id or '（无）'}",
                f"- 考生号：{exam_display}",
                f"- 通知书：{s.notice_no or '（无）'}",
                f"- 专业：{s.major or '（无）'}",
                f"- 书院：{s.academy or '（无）'}",
                f"- 状态：{s.status or '（无）'}",
            ]
        )
    elif result.sid_hits or result.exam_hits or result.name_hits or result.notice_hits:
        lines.append("")
        lines.append("部分匹配（非 strong）：")
        for s in result.sid_hits[:3]:
            lines.append(
                f"- 同学号：{s.name} / {s.student_id} / {s.major or '（无专业）'}"
            )
        for s in result.exam_hits[:3]:
            exam_display = mask_exam_no(s.exam_no) if s.exam_no else "（无）"
            lines.append(
                f"- 同考生号：{s.name} / {s.student_id or '（无学号）'} / "
                f"{exam_display} / {s.major or '（无专业）'}"
            )
        for s in result.notice_hits[:3]:
            lines.append(
                f"- 同通知书：{s.name} / {s.student_id or '（无学号）'} / "
                f"{s.notice_no} / {s.major or '（无专业）'}"
            )
        for s in result.name_hits[:3]:
            if any(
                student_ids_match(s.student_id or "", h.student_id or "")
                for h in result.sid_hits
                if s.student_id and h.student_id
            ):
                continue
            if any(
                exam_nos_match(s.exam_no or "", h.exam_no or "")
                for h in result.exam_hits
                if s.exam_no and h.exam_no
            ):
                continue
            lines.append(
                f"- 同姓名：{s.name} / {s.student_id or '（无学号）'} / {s.major or '（无专业）'}"
            )
    else:
        lines.append("")
        lines.append(
            "缓存中无同名/同学号/同考生号记录。可先 /audit sync 或 /audit catchup preview。"
        )

    lines.extend(
        [
            "",
            "用法：",
            "/audit lookup 张三 261220001",
            "/audit lookup 张三 20260001",
            "/audit lookup 张三 26123456000001",
            "/audit lookup 张三 26123456000001 计算机科学与技术",
            "",
            "说明：支持姓名+学号 / 通知书编号 / 考生号；不修改任何申请。",
        ]
    )
    return "\n".join(lines)


def format_lookup_help() -> str:
    return "\n".join(
        [
            "校对表查询（不修改任何申请）",
            "",
            "用法：",
            "/audit lookup 张三 261220001",
            "/audit lookup 张三 20260001",
            "/audit lookup 张三 26123456000001",
            "/audit lookup 张三 26123456000001 计算机科学与技术",
            "",
            "说明：",
            "- 支持姓名+学号 / 通知书编号 / 考生号",
            "- 用当前本地缓存比对，返回 strong/weak/none 与部分命中",
            "- 不调用 QQ，也不改 pending",
            "- 名单过旧请先 /audit sync",
        ]
    )
