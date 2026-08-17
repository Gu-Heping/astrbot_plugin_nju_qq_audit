"""Roster-assisted parse completion for compact graduate join comments."""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.normalize import normalize_name, normalize_whitespace
from core.parser import extract_answer_segment
from graduate.models import GraduateParsedApplication, GraduateStudent

_AMBIGUOUS_TYPE_PATTERN = re.compile(
    r"(硕\s*[/／或]\s*博|博\s*[/／或]\s*硕|"
    r"硕士\s*[/／或]\s*博士|博士\s*[/／或]\s*硕士|硕博)",
    re.IGNORECASE,
)

# (tail token, normalized admission_type); longer tokens first when matching.
_TAIL_TYPE_TOKENS: list[tuple[str, str]] = sorted(
    [
        ("博士生", "博士"),
        ("硕士生", "硕士"),
        ("博士", "博士"),
        ("硕士", "硕士"),
        ("直博", "博士"),
        ("专硕", "硕士"),
        ("学硕", "硕士"),
        ("phd", "博士"),
        ("master", "硕士"),
        ("博", "博士"),
        ("硕", "硕士"),
    ],
    key=lambda item: -len(item[0]),
)


@dataclass(frozen=True)
class GraduateRosterParseContext:
    unique_names: list[str]
    majors_by_name: dict[str, set[str]]
    name_pattern: re.Pattern[str] | None = None


def _compact_text(raw: str) -> str:
    segment = extract_answer_segment(raw)
    text = normalize_whitespace(segment or raw)
    return text.replace(" ", "").replace("　", "")


def _unique_roster_names(students: list[GraduateStudent]) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for student in students:
        name = normalize_name(student.name)
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    names.sort(key=len, reverse=True)
    return names


def _find_name_spans(
    text: str,
    unique_names: list[str],
    name_pattern: re.Pattern[str] | None = None,
) -> list[tuple[str, int, int]]:
    candidates: list[tuple[str, int, int]] = []
    if name_pattern is not None:
        for match in name_pattern.finditer(text):
            name = match.group(1)
            if name:
                start = match.start()
                candidates.append((name, start, start + len(name)))
    else:
        for name in unique_names:
            if not name:
                continue
            start = 0
            while True:
                idx = text.find(name, start)
                if idx == -1:
                    break
                candidates.append((name, idx, idx + len(name)))
                start = idx + 1
    candidates.sort(key=lambda item: (-(item[2] - item[1]), item[1]))
    selected: list[tuple[str, int, int]] = []
    occupied: set[int] = set()
    for name, start, end in candidates:
        if any(pos in occupied for pos in range(start, end)):
            continue
        selected.append((name, start, end))
        for pos in range(start, end):
            occupied.add(pos)
    selected.sort(key=lambda item: item[1])
    return selected


def _locate_name_span(
    text: str,
    name: str,
    unique_names: list[str],
    name_pattern: re.Pattern[str] | None = None,
) -> tuple[str, int, int] | None:
    target = normalize_name(name)
    if not target:
        return None
    spans = _find_name_spans(text, unique_names, name_pattern)
    for span_name, start, end in spans:
        if normalize_name(span_name) == target:
            return span_name, start, end
    if target in unique_names and target in text:
        idx = text.find(target)
        if idx != -1:
            return target, idx, idx + len(target)
    return None


def _ends_with_token(text: str, token: str) -> bool:
    if not text or not token:
        return False
    if text.endswith(token):
        return True
    return text.lower().endswith(token.lower())


def _roster_major_names(students: list[GraduateStudent], name: str) -> set[str]:
    target = normalize_name(name)
    majors: set[str] = set()
    for student in students:
        if normalize_name(student.name) != target:
            continue
        major = normalize_whitespace(student.major_name).replace(" ", "").replace("　", "")
        if major:
            majors.add(major)
    return majors


def build_graduate_roster_parse_context(
    students: list[GraduateStudent],
) -> GraduateRosterParseContext:
    majors_by_name: dict[str, set[str]] = {}
    for student in students:
        name = normalize_name(student.name)
        if not name:
            continue
        major = normalize_whitespace(student.major_name).replace(" ", "").replace("　", "")
        if major:
            majors_by_name.setdefault(name, set()).add(major)
        else:
            majors_by_name.setdefault(name, set())
    names = sorted(majors_by_name.keys(), key=len, reverse=True)
    name_pattern = (
        re.compile("(?=(" + "|".join(re.escape(name) for name in names) + "))")
        if names
        else None
    )
    return GraduateRosterParseContext(
        unique_names=names,
        majors_by_name=majors_by_name,
        name_pattern=name_pattern,
    )


def _major_matches_roster(major: str, roster_majors: set[str]) -> bool:
    if not major or not roster_majors:
        return False
    compact = major.replace(" ", "").replace("　", "")
    if compact in roster_majors:
        return True
    return any(compact in roster_major or roster_major in compact for roster_major in roster_majors)


def _peel_tail_token(text: str, token: str) -> str:
    if token.isascii():
        return text[: len(text) - len(token)].strip()
    return text[: -len(token)].strip()


def _parse_tail_type(
    remaining: str,
    roster_majors: set[str] | None = None,
) -> tuple[str | None, str | None, str]:
    text = remaining.strip()
    if not text:
        return None, None, text
    if _AMBIGUOUS_TYPE_PATTERN.search(text):
        return None, None, text

    candidates: list[tuple[str, str, str]] = []
    for token, admission in _TAIL_TYPE_TOKENS:
        if not _ends_with_token(text, token):
            continue
        major_text = _peel_tail_token(text, token)
        candidates.append((admission, token, major_text))

    if not candidates:
        return None, None, text

    roster_majors = roster_majors or set()

    def _score(item: tuple[str, str, str]) -> tuple[int, int, int]:
        admission, token, major_text = item
        roster_hit = 1 if _major_matches_roster(major_text, roster_majors) else 0
        return (roster_hit, len(major_text), len(token))

    admission, token, major_text = max(candidates, key=_score)

    peel = major_text
    for other_token, other_adm in _TAIL_TYPE_TOKENS:
        if other_token == token:
            continue
        if _ends_with_token(peel, other_token) and other_adm != admission:
            return None, None, remaining.strip()

    return admission, token, major_text


def _should_replace_major_text(
    parsed: GraduateParsedApplication,
    compact_text: str,
    name: str,
    new_major: str,
) -> bool:
    if not new_major or len(new_major) < 2:
        return False
    if not parsed.major_text:
        return True
    existing = parsed.major_text.replace(" ", "").replace("　", "")
    if existing == compact_text:
        return True
    if name and name in existing:
        return True
    if new_major in existing and len(new_major) < len(existing):
        return True
    return False


def complete_graduate_parse_from_roster(
    parsed: GraduateParsedApplication,
    students: list[GraduateStudent],
    *,
    context: GraduateRosterParseContext | None = None,
) -> GraduateParsedApplication:
    if not students:
        return parsed

    text = _compact_text(parsed.raw)
    if not text:
        return parsed

    if context is None:
        context = build_graduate_roster_parse_context(students)
    unique_names = context.unique_names
    if not unique_names:
        return parsed

    spans = _find_name_spans(text, unique_names, context.name_pattern)
    distinct = {name for name, _, _ in spans}

    if len(distinct) > 1:
        if "multiple roster names" not in parsed.parse_errors:
            parsed.parse_errors.append("multiple roster names")
        return parsed

    if len(distinct) == 0:
        if parsed.name and normalize_name(parsed.name) not in unique_names:
            parsed.name = None
        return parsed

    if not parsed.name and len(distinct) == 1:
        parsed.name = normalize_name(next(iter(distinct)))

    if not parsed.name:
        return parsed

    located = _locate_name_span(
        text,
        parsed.name,
        unique_names,
        context.name_pattern,
    )
    if located is None:
        return parsed

    _, start, end = located
    remaining = (text[:start] + text[end:]).strip()
    roster_majors = context.majors_by_name.get(normalize_name(parsed.name), set())
    admission_type, admission_raw, major_text = _parse_tail_type(remaining, roster_majors)

    if not admission_type and remaining and len(remaining) >= 2:
        if _major_matches_roster(remaining, roster_majors):
            major_text = remaining

    if not parsed.admission_type and admission_type:
        parsed.admission_type = admission_type
    if not parsed.admission_type_raw and admission_raw:
        parsed.admission_type_raw = admission_raw
    if major_text and len(major_text) >= 2 and _should_replace_major_text(
        parsed, text, parsed.name, major_text
    ):
        parsed.major_text = major_text

    return parsed
