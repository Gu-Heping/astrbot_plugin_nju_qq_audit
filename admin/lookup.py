"""Manual student-table lookup for admin diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from core.decision import make_decision
from core.matcher import MatchResult, match_student
from core.normalize import normalize_name, normalize_student_id, names_match, student_ids_match
from core.parser import ParsedApplication
from data_source.njutable_provider import load_students_for_audit
from data_source.students import Student


@dataclass
class LookupResult:
    name: str | None
    student_id: str | None
    major: str | None
    notice_no: str | None
    cache_count: int
    match: MatchResult
    name_hits: list[Student]
    sid_hits: list[Student]


def _sid_hits(students: list[Student], student_id: str | None) -> list[Student]:
    if not student_id:
        return []
    return [
        s
        for s in students
        if s.student_id and student_ids_match(student_id, s.student_id)
    ]


def _name_hits(students: list[Student], name: str | None, *, limit: int = 5) -> list[Student]:
    if not name:
        return []
    hits = [s for s in students if names_match(name, s.name)]
    return hits[:limit]


def run_lookup(
    settings,
    cache,
    *,
    name: str | None = None,
    student_id: str | None = None,
    major: str | None = None,
    notice_no: str | None = None,
) -> LookupResult:
    students = load_students_for_audit(settings, cache)
    parsed = ParsedApplication(
        raw="",
        name=normalize_name(name) if name else None,
        student_id=normalize_student_id(student_id) if student_id else None,
        major=(major or "").strip() or None,
        notice_no=(notice_no or "").strip() or None,
    )
    if parsed.name == "":
        parsed.name = None
    if parsed.student_id == "":
        parsed.student_id = None
    match = match_student(parsed, students)
    return LookupResult(
        name=parsed.name,
        student_id=parsed.student_id,
        major=parsed.major,
        notice_no=parsed.notice_no,
        cache_count=len(students),
        match=match,
        name_hits=_name_hits(students, parsed.name),
        sid_hits=_sid_hits(students, parsed.student_id),
    )


def parse_lookup_args(args: str) -> tuple[str | None, str | None, str | None]:
    """Parse `姓名 学号 [专业...]` from remaining command text."""
    text = (args or "").strip()
    if not text:
        return None, None, None
    parts = text.split()
    name: str | None = None
    student_id: str | None = None
    major_parts: list[str] = []

    for part in parts:
        digits = normalize_student_id(part)
        if not student_id and digits and len(digits) >= 6 and digits[:1].isdigit():
            # Prefer explicit student-id shaped tokens
            if len(digits) >= 8 or part.isdigit() or digits.startswith("26"):
                student_id = digits
                continue
        if not name and all("\u4e00" <= c <= "\u9fff" or c == "·" for c in part) and 2 <= len(part) <= 4:
            name = normalize_name(part)
            continue
        major_parts.append(part)

    # Allow "张三261220001" glued name+id
    if not student_id and name is None and len(parts) == 1:
        from core.parser import parse_application_comment

        parsed = parse_application_comment(parts[0])
        return parsed.name, parsed.student_id, parsed.major

    major = " ".join(major_parts).strip() or None
    return name, student_id, major


def format_lookup_result(result: LookupResult) -> str:
    decision = make_decision(
        ParsedApplication(
            raw="",
            name=result.name,
            student_id=result.student_id,
            major=result.major,
            notice_no=result.notice_no,
        ),
        result.match,
        is_target_group=True,
    )
    lines = [
        "校对表查询",
        "",
        f"缓存人数：{result.cache_count}",
        f"查询姓名：{result.name or '（无）'}",
        f"查询学号：{result.student_id or '（无）'}",
        f"查询专业：{result.major or '（无）'}",
        "",
        f"匹配强度：{result.match.strength}",
        f"原因：{result.match.reason}",
        f"决策建议：{decision.decision}",
    ]
    if result.match.matched_student is not None:
        s = result.match.matched_student
        lines.extend(
            [
                "",
                "命中记录：",
                f"- 姓名：{s.name}",
                f"- 学号：{s.student_id or '（无）'}",
                f"- 通知书：{s.notice_no or '（无）'}",
                f"- 专业：{s.major or '（无）'}",
                f"- 书院：{s.academy or '（无）'}",
                f"- 状态：{s.status or '（无）'}",
            ]
        )
    elif result.sid_hits or result.name_hits:
        lines.append("")
        lines.append("部分匹配（非 strong）：")
        for s in result.sid_hits[:3]:
            lines.append(
                f"- 同学号：{s.name} / {s.student_id} / {s.major or '（无专业）'}"
            )
        for s in result.name_hits[:3]:
            if any(student_ids_match(s.student_id or "", h.student_id or "") for h in result.sid_hits if s.student_id and h.student_id):
                continue
            lines.append(
                f"- 同姓名：{s.name} / {s.student_id or '（无学号）'} / {s.major or '（无专业）'}"
            )
    else:
        lines.append("")
        lines.append("缓存中无同名或同学号记录。可先 /audit sync 或 /audit catchup preview。")

    lines.extend(
        [
            "",
            "用法：/audit lookup 张三 261220001",
            "     /audit lookup 张三 261220001 计算机科学与技术",
        ]
    )
    return "\n".join(lines)


def format_lookup_help() -> str:
    return "\n".join(
        [
            "校对表查询（不修改任何申请）",
            "",
            "用法：",
            "/audit lookup <姓名> <学号> [专业]",
            "",
            "示例：",
            "/audit lookup 张三 261220001",
            "/audit lookup 张三 261220001 计算机科学与技术",
            "",
            "说明：",
            "- 用当前本地缓存比对，返回 strong/weak/none 与部分命中",
            "- 不调用 QQ，也不改 pending",
            "- 名单过旧请先 /audit sync",
        ]
    )
