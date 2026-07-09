from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.aliases import is_known_major_token
from core.normalize import (
    looks_like_qq_token,
    normalize_name,
    normalize_notice_no,
    normalize_student_id,
    normalize_whitespace,
)

STUDENT_ID_PATTERN = re.compile(r"\b(2[0-9]1\d{6})\b")
NOTICE_NO_PATTERN = re.compile(r"\b(202[56]\d{4})\b")
LOOSE_TOKEN_PATTERN = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9\-_/]{3,31})\b")
NAME_LABEL_PATTERN = re.compile(
    r"(?:姓名|名字|真实姓名)[:：\s]*([\u4e00-\u9fa5·]{2,4})(?=\s|学号|通知书|编号|专业|$|[:：])",
    re.IGNORECASE,
)
STUDENT_ID_LABEL_PATTERN = re.compile(r"(?:学号|student\s*id)[:：\s]*(\d{6,12})", re.IGNORECASE)
NOTICE_LABEL_PATTERN = re.compile(
    r"(?:录取通知书编号|通知书编号|通知书号|录取通知书|录取编号)"
    r"[:：\s]*([A-Za-z0-9][A-Za-z0-9\-_/]{3,31})",
    re.IGNORECASE,
)
NOTICE_SHORT_LABEL_PATTERN = re.compile(
    r"(?:^|[\s,，])编号[:：\s]*([A-Za-z0-9][A-Za-z0-9\-_/]{3,31})",
    re.IGNORECASE,
)
MAJOR_LABEL_PATTERN = re.compile(
    r"(?:专业|录取专业|报读专业)[:：\s]*([\u4e00-\u9fa5a-zA-Z（）()·\-]{2,30})",
    re.IGNORECASE,
)
ACADEMY_LABEL_PATTERN = re.compile(
    r"(?:书院|学院|归属书院)[:：\s]*([\u4e00-\u9fa5a-zA-Z（）()·\-]{2,20})",
    re.IGNORECASE,
)
CHINESE_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")


@dataclass
class ParsedApplication:
    raw: str
    name: str | None = None
    student_id: str | None = None
    notice_no: str | None = None
    major: str | None = None
    academy: str | None = None
    notice_no_candidates: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


def _add_notice_candidate(result: ParsedApplication, value: str) -> None:
    norm = normalize_notice_no(value)
    if not norm:
        return
    if result.student_id and normalize_student_id(result.student_id) == normalize_student_id(norm):
        return
    if looks_like_qq_token(value):
        return
    if norm not in result.notice_no_candidates:
        result.notice_no_candidates.append(norm)
    if not result.notice_no:
        result.notice_no = norm


def _finalize_notice_candidates(result: ParsedApplication) -> None:
    if result.notice_no and result.notice_no not in result.notice_no_candidates:
        result.notice_no_candidates.insert(0, result.notice_no)
    unique = []
    seen: set[str] = set()
    for item in result.notice_no_candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    result.notice_no_candidates = unique
    if len(unique) > 1:
        norms = set(unique)
        if len(norms) > 1:
            result.parse_errors.append("multiple notice_no candidates")


def parse_application_comment(raw: str) -> ParsedApplication:
    text = normalize_whitespace(raw)
    result = ParsedApplication(raw=text)
    if not text:
        result.parse_errors.append("empty comment")
        return result

    _extract_compact_credentials(text, result)

    name_label = NAME_LABEL_PATTERN.search(text)
    if name_label:
        result.name = normalize_name(name_label.group(1))

    sid_label = STUDENT_ID_LABEL_PATTERN.search(text)
    if sid_label:
        result.student_id = normalize_student_id(sid_label.group(1))

    notice_label = NOTICE_LABEL_PATTERN.search(text)
    if notice_label:
        _add_notice_candidate(result, notice_label.group(1))
    else:
        short_notice = NOTICE_SHORT_LABEL_PATTERN.search(text)
        if short_notice:
            _add_notice_candidate(result, short_notice.group(1))

    major_label = MAJOR_LABEL_PATTERN.search(text)
    if major_label:
        result.major = major_label.group(1).strip()

    academy_label = ACADEMY_LABEL_PATTERN.search(text)
    if academy_label:
        result.academy = academy_label.group(1).strip()

    if not result.student_id:
        sid_match = STUDENT_ID_PATTERN.search(text)
        if sid_match:
            result.student_id = sid_match.group(1)

    if not result.notice_no:
        notice_match = NOTICE_NO_PATTERN.search(text)
        if notice_match and notice_match.group(1) != result.student_id:
            _add_notice_candidate(result, notice_match.group(1))

    for loose in LOOSE_TOKEN_PATTERN.findall(text):
        if STUDENT_ID_PATTERN.fullmatch(loose):
            continue
        if looks_like_qq_token(loose):
            continue
        _add_notice_candidate(result, loose)

    if not result.name or not result.major:
        _parse_by_tokens(text, result)

    if not result.name and not result.student_id and not result.notice_no and not result.major:
        result.parse_errors.append("unable to parse any field")

    _finalize_notice_candidates(result)
    return result


def _extract_compact_credentials(text: str, result: ParsedApplication) -> None:
    if not result.student_id:
        sid_compact = re.search(r"([\u4e00-\u9fa5·]{2,4})(2[0-9]1\d{6})", text)
        if sid_compact:
            if not result.name:
                result.name = normalize_name(sid_compact.group(1))
            result.student_id = sid_compact.group(2)

    if not result.notice_no:
        notice_compact = re.search(r"([\u4e00-\u9fa5·]{2,4})(202[56]\d{4})", text)
        if notice_compact:
            if not result.name:
                result.name = normalize_name(notice_compact.group(1))
            if not result.student_id or notice_compact.group(2) != result.student_id:
                _add_notice_candidate(result, notice_compact.group(2))


def _split_mixed_token(token: str) -> dict[str, str]:
    sid_match = re.match(r"^([\u4e00-\u9fa5·]{2,4})(2[0-9]1\d{6})$", token)
    if sid_match:
        return {"name": normalize_name(sid_match.group(1)), "student_id": sid_match.group(2)}
    notice_match = re.match(r"^([\u4e00-\u9fa5·]{2,4})(202[56]\d{4})$", token)
    if notice_match:
        return {
            "name": normalize_name(notice_match.group(1)),
            "notice_no": normalize_notice_no(notice_match.group(2)),
        }
    return {}


def _parse_by_tokens(text: str, result: ParsedApplication) -> None:
    tokens = [t for t in re.split(r"[\s,，、；;|]+", text) if t]
    majors: list[str] = []
    names: list[str] = []

    for token in tokens:
        split = _split_mixed_token(token)
        if split.get("student_id") and not result.student_id:
            result.student_id = split["student_id"]
        if split.get("notice_no") and not result.notice_no:
            _add_notice_candidate(result, split["notice_no"])
        if split.get("name"):
            names.append(split["name"])
            continue

        if not result.student_id:
            sid_match = STUDENT_ID_PATTERN.search(token)
            if sid_match:
                result.student_id = sid_match.group(1)
                continue
        if not result.notice_no:
            notice_match = NOTICE_NO_PATTERN.search(token)
            if notice_match and notice_match.group(1) != result.student_id:
                _add_notice_candidate(result, notice_match.group(1))
                continue
        if is_known_major_token(token):
            majors.append(token)
            continue
        if CHINESE_NAME_PATTERN.match(token) and not names:
            names.append(normalize_name(token))
            continue
        if re.search(r"[\u4e00-\u9fa5]", token) and len(token) >= 2:
            if not names:
                names.append(normalize_name(token))
            else:
                majors.append(token)

    if not result.name and names:
        result.name = names[0]
    if not result.major and majors:
        result.major = majors[0]
