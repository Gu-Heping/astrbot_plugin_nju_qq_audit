from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.aliases import is_known_major_token
from core.normalize import (
    is_exam_no_shape,
    looks_like_qq_token,
    normalize_exam_no,
    normalize_name,
    normalize_notice_no,
    normalize_student_id,
    normalize_whitespace,
)

STUDENT_ID_PATTERN = re.compile(r"\b(261\d{6})\b")
STUDENT_ID_SHORT_PATTERN = re.compile(r"\b(261\d{5})\b")
STUDENT_ID_LEGACY_PATTERN = re.compile(r"\b(2[0-9]1\d{6})\b")
NOTICE_NO_PATTERN = re.compile(r"\b(202[56]\d{4})\b")
EXAM_NO_PATTERN = re.compile(r"\b(2[0-9]\d{12})\b")
LOOSE_TOKEN_PATTERN = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9\-_/]{3,31})\b")
# QQ 答案常见分隔：空白、中英文标点，以及 + / ＋ ／
FIELD_SEPARATOR_PATTERN = re.compile(r"[\s,，、；;|+＋/／]+")
NAME_LABEL_PATTERN = re.compile(
    r"(?:姓名|名字|真实姓名)[:：\s]+([\u4e00-\u9fa5·]{2,4})"
    r"(?=\s|学号|考生号|准考证|报名号|通知书|编号|专业|$|[:：])",
    re.IGNORECASE,
)
STUDENT_ID_LABEL_PATTERN = re.compile(r"(?:学号|student\s*id)[:：\s]*(\d{6,12})", re.IGNORECASE)
EXAM_NO_LABEL_PATTERN = re.compile(
    r"(?:高考考生号|考生号|准考证号|报名号)[:：\s]*(\d{10,16})",
    re.IGNORECASE,
)
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
ANSWER_MARKER_PATTERN = re.compile(
    r"(?:答案|回答|答|A|answer)\s*[：:]",
    re.IGNORECASE,
)
QUESTION_LINE_PATTERN = re.compile(
    r"^[\s\S]*?问题\s*[：:][^\n]*(?:\n|$)",
    re.IGNORECASE,
)
VERIFY_PREFIX_PATTERN = re.compile(r"^验证\s*[：:]\s*", re.IGNORECASE)

_TEMPLATE_TOKENS = frozenset(
    {
        "问题",
        "问题：姓名",
        "姓名",
        "学号",
        "录取号",
        "学号/录取号",
        "考生号",
        "准考证号",
        "报名号",
        "专业",
        "答案",
        "答",
        "回答",
        "a",
        "answer",
    }
)

# Compact 「汉字+学号」不得把字段标签当成姓名
_NON_PERSON_NAME_TOKENS = frozenset(
    {
        "学号",
        "姓名",
        "名字",
        "专业",
        "书院",
        "学院",
        "编号",
        "通知书",
        "录取号",
        "录取",
        "考生号",
        "准考证号",
        "报名号",
        "答案",
        "回答",
        "问题",
    }
)

SELF_INTRO_NAME_PATTERN = re.compile(
    r"(?:我是|我叫|本人是|名叫)([\u4e00-\u9fa5·]{2,4})"
    r"(?=$|[，,。.\s；;、]|学号|考生号|专业|书院)"
)


@dataclass
class ParsedApplication:
    raw: str
    name: str | None = None
    student_id: str | None = None
    exam_no: str | None = None
    notice_no: str | None = None
    major: str | None = None
    academy: str | None = None
    notice_no_candidates: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


def extract_answer_segment(raw: str) -> str:
    """从 QQ 入群验证 raw comment 中提取答案段（先于 normalize_whitespace）。"""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    matches = list(ANSWER_MARKER_PATTERN.finditer(text))
    if matches:
        return text[matches[-1].end() :].strip()

    stripped = QUESTION_LINE_PATTERN.sub("", text, count=1).strip()
    if stripped != text:
        return stripped

    return VERIFY_PREFIX_PATTERN.sub("", text).strip()


def _is_template_token(token: str) -> bool:
    value = (token or "").strip()
    if not value:
        return True
    if value in _TEMPLATE_TOKENS:
        return True
    lower = value.lower()
    if lower in _TEMPLATE_TOKENS:
        return True
    if re.match(r"^问题[：:]", value):
        return True
    if re.fullmatch(r"学号/录取号", value):
        return True
    if value.startswith("问题："):
        return True
    return False


def _add_notice_candidate(result: ParsedApplication, value: str) -> None:
    if is_exam_no_shape(value):
        return
    norm = normalize_notice_no(value)
    if not norm:
        return
    if is_exam_no_shape(norm):
        return
    if result.student_id and normalize_student_id(result.student_id) == normalize_student_id(norm):
        return
    if result.exam_no and normalize_exam_no(result.exam_no) == normalize_exam_no(norm):
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
        if is_exam_no_shape(item):
            continue
        if item not in seen:
            seen.add(item)
            unique.append(item)
    result.notice_no_candidates = unique
    if result.notice_no and is_exam_no_shape(result.notice_no):
        result.notice_no = unique[0] if unique else None
    if len(unique) > 1:
        norms = set(unique)
        if len(norms) > 1:
            result.parse_errors.append("multiple notice_no candidates")


def _assign_exam_no(result: ParsedApplication, value: str) -> None:
    normalized = normalize_exam_no(value)
    if not is_exam_no_shape(normalized):
        return
    if result.exam_no:
        return
    result.exam_no = normalized


def _assign_student_id(result: ParsedApplication, value: str) -> None:
    normalized = normalize_student_id(value)
    if not normalized:
        return
    # Never put 14-digit exam numbers into student_id.
    if is_exam_no_shape(normalized):
        _assign_exam_no(result, normalized)
        return
    if result.student_id:
        return
    result.student_id = normalized


def _find_exam_no_in_text(text: str) -> str | None:
    match = EXAM_NO_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


def _find_student_id_in_text(text: str) -> str | None:
    for pattern in (STUDENT_ID_PATTERN, STUDENT_ID_SHORT_PATTERN, STUDENT_ID_LEGACY_PATTERN):
        match = pattern.search(text)
        if not match:
            continue
        candidate = match.group(1)
        start, end = match.span(1)
        # Reject prefix of a longer 14-digit exam number.
        if end < len(text) and text[end].isdigit():
            window = re.sub(r"\D", "", text[start : start + 20])
            if is_exam_no_shape(window[:14]):
                continue
        if is_exam_no_shape(candidate):
            continue
        return candidate
    return None


def _looks_like_person_name(value: str | None) -> bool:
    if not value:
        return False
    name = normalize_name(value)
    if not CHINESE_NAME_PATTERN.match(name):
        return False
    if name in _NON_PERSON_NAME_TOKENS or name in _TEMPLATE_TOKENS:
        return False
    if _is_template_token(name):
        return False
    return True


def _assign_name_if_better(result: ParsedApplication, value: str) -> None:
    name = normalize_name(value)
    if not _looks_like_person_name(name):
        return
    if not result.name or not _looks_like_person_name(result.name):
        result.name = name


def parse_application_comment(raw: str) -> ParsedApplication:
    full = normalize_whitespace(raw)
    text = normalize_whitespace(extract_answer_segment(raw))
    result = ParsedApplication(raw=full)
    if not text:
        result.parse_errors.append("empty comment")
        return result

    _extract_compact_credentials(text, result)

    name_label = NAME_LABEL_PATTERN.search(text)
    if name_label:
        _assign_name_if_better(result, name_label.group(1))

    intro = SELF_INTRO_NAME_PATTERN.search(text)
    if intro:
        _assign_name_if_better(result, intro.group(1))

    sid_label = STUDENT_ID_LABEL_PATTERN.search(text)
    if sid_label:
        _assign_student_id(result, sid_label.group(1))

    exam_label = EXAM_NO_LABEL_PATTERN.search(text)
    if exam_label:
        _assign_exam_no(result, exam_label.group(1))

    notice_label = NOTICE_LABEL_PATTERN.search(text)
    if notice_label:
        _add_notice_candidate(result, notice_label.group(1))
    else:
        short_notice = NOTICE_SHORT_LABEL_PATTERN.search(text)
        if short_notice:
            _add_notice_candidate(result, short_notice.group(1))

    major_label = MAJOR_LABEL_PATTERN.search(text)
    if major_label:
        major = major_label.group(1).strip()
        if major.startswith("是"):
            major = major[1:].strip()
        result.major = major or result.major

    academy_label = ACADEMY_LABEL_PATTERN.search(text)
    if academy_label:
        result.academy = academy_label.group(1).strip()

    if not result.exam_no:
        exam = _find_exam_no_in_text(text)
        if exam:
            _assign_exam_no(result, exam)

    if not result.student_id:
        sid = _find_student_id_in_text(text)
        if sid:
            _assign_student_id(result, sid)

    if not result.notice_no:
        notice_match = NOTICE_NO_PATTERN.search(text)
        if notice_match and notice_match.group(1) != result.student_id:
            _add_notice_candidate(result, notice_match.group(1))

    if not result.student_id:
        for loose in LOOSE_TOKEN_PATTERN.findall(text):
            if is_exam_no_shape(loose):
                continue
            if STUDENT_ID_PATTERN.fullmatch(loose):
                continue
            if looks_like_qq_token(loose):
                continue
            norm = normalize_notice_no(loose)
            if not norm:
                continue
            if NOTICE_NO_PATTERN.fullmatch(norm):
                _add_notice_candidate(result, loose)
            elif re.search(r"[A-Za-z]", loose):
                _add_notice_candidate(result, loose)

    if not result.name or not result.major or not _looks_like_person_name(result.name):
        _parse_by_tokens(text, result)

    if result.name and not _looks_like_person_name(result.name):
        result.name = None
        _parse_by_tokens(text, result)

    if (
        not result.name
        and not result.student_id
        and not result.exam_no
        and not result.notice_no
        and not result.major
    ):
        result.parse_errors.append("unable to parse any field")

    _finalize_notice_candidates(result)
    return result


def _extract_compact_credentials(text: str, result: ParsedApplication) -> None:
    if not result.exam_no:
        exam_compact = re.search(
            r"([\u4e00-\u9fa5·]{2,4})(2[0-9]\d{12})",
            text,
        )
        if exam_compact:
            candidate_name = exam_compact.group(1)
            if _looks_like_person_name(candidate_name):
                _assign_name_if_better(result, candidate_name)
            _assign_exam_no(result, exam_compact.group(2))

    if not result.student_id:
        # (?!\d) avoids taking a 9-digit prefix of a 14-digit exam number.
        sid_compact = re.search(r"([\u4e00-\u9fa5·]{2,4})(2[0-9]1\d{5,6})(?!\d)", text)
        if sid_compact:
            candidate_name = sid_compact.group(1)
            if _looks_like_person_name(candidate_name):
                _assign_name_if_better(result, candidate_name)
            _assign_student_id(result, sid_compact.group(2))

    if not result.notice_no:
        notice_compact = re.search(r"([\u4e00-\u9fa5·]{2,4})(202[56]\d{4})(?!\d)", text)
        if notice_compact:
            candidate_name = notice_compact.group(1)
            if _looks_like_person_name(candidate_name):
                _assign_name_if_better(result, candidate_name)
            if not result.student_id or notice_compact.group(2) != result.student_id:
                _add_notice_candidate(result, notice_compact.group(2))


def _split_mixed_token(token: str) -> dict[str, str]:
    glued_exam_major = re.match(
        r"^([\u4e00-\u9fa5·]{2,4})(2[0-9]\d{12})([\u4e00-\u9fa5a-zA-Z（）()·\-]{2,30})$",
        token,
    )
    if glued_exam_major:
        out = {
            "exam_no": normalize_exam_no(glued_exam_major.group(2)),
            "major": glued_exam_major.group(3).strip(),
        }
        if _looks_like_person_name(glued_exam_major.group(1)):
            out["name"] = normalize_name(glued_exam_major.group(1))
        return out

    exam_match = re.match(r"^([\u4e00-\u9fa5·]{2,4})(2[0-9]\d{12})$", token)
    if exam_match:
        out = {"exam_no": normalize_exam_no(exam_match.group(2))}
        if _looks_like_person_name(exam_match.group(1)):
            out["name"] = normalize_name(exam_match.group(1))
        return out

    glued_major = re.match(
        r"^([\u4e00-\u9fa5·]{2,4})(2[0-9]1\d{5,6})([\u4e00-\u9fa5a-zA-Z（）()·\-]{2,30})$",
        token,
    )
    if glued_major:
        out = {
            "student_id": glued_major.group(2),
            "major": glued_major.group(3).strip(),
        }
        if _looks_like_person_name(glued_major.group(1)):
            out["name"] = normalize_name(glued_major.group(1))
        return out

    sid_match = re.match(r"^([\u4e00-\u9fa5·]{2,4})(261\d{5,6})$", token)
    if sid_match:
        out = {"student_id": sid_match.group(2)}
        if _looks_like_person_name(sid_match.group(1)):
            out["name"] = normalize_name(sid_match.group(1))
        return out
    sid_match = re.match(r"^([\u4e00-\u9fa5·]{2,4})(2[0-9]1\d{6})$", token)
    if sid_match:
        out = {"student_id": sid_match.group(2)}
        if _looks_like_person_name(sid_match.group(1)):
            out["name"] = normalize_name(sid_match.group(1))
        return out
    notice_match = re.match(r"^([\u4e00-\u9fa5·]{2,4})(202[56]\d{4})$", token)
    if notice_match:
        out = {"notice_no": normalize_notice_no(notice_match.group(2))}
        if _looks_like_person_name(notice_match.group(1)):
            out["name"] = normalize_name(notice_match.group(1))
        return out
    return {}


def _parse_by_tokens(text: str, result: ParsedApplication) -> None:
    tokens = [t for t in FIELD_SEPARATOR_PATTERN.split(text) if t]
    majors: list[str] = []
    names: list[str] = []

    for token in tokens:
        if _is_template_token(token):
            continue

        split = _split_mixed_token(token)
        if split.get("student_id") and not result.student_id:
            _assign_student_id(result, split["student_id"])
        if split.get("exam_no") and not result.exam_no:
            _assign_exam_no(result, split["exam_no"])
        if split.get("notice_no") and not result.notice_no:
            _add_notice_candidate(result, split["notice_no"])
        if split.get("major") and not result.major:
            result.major = split["major"]
        if split.get("name"):
            names.append(split["name"])
            continue

        # Credential priority: student_id → exam_no → notice_no → name → major
        sid = _find_student_id_in_text(token)
        if sid:
            if not result.student_id:
                _assign_student_id(result, sid)
            continue
        exam = _find_exam_no_in_text(token)
        if exam:
            if not result.exam_no:
                _assign_exam_no(result, exam)
            continue
        if is_exam_no_shape(token):
            if not result.exam_no:
                _assign_exam_no(result, token)
            continue
        notice_match = NOTICE_NO_PATTERN.fullmatch(token) or NOTICE_NO_PATTERN.search(token)
        if notice_match and notice_match.group(1) != result.student_id:
            if not result.notice_no:
                _add_notice_candidate(result, notice_match.group(1))
            continue
        if is_known_major_token(token):
            majors.append(token)
            continue
        if CHINESE_NAME_PATTERN.match(token) and not names:
            if _looks_like_person_name(token):
                names.append(normalize_name(token))
            continue
        intro = SELF_INTRO_NAME_PATTERN.match(token) or SELF_INTRO_NAME_PATTERN.search(token)
        if intro and _looks_like_person_name(intro.group(1)):
            names.append(normalize_name(intro.group(1)))
            continue
        if re.search(r"[\u4e00-\u9fa5]", token) and len(token) >= 2:
            if not names and _looks_like_person_name(token):
                names.append(normalize_name(token))
            else:
                majors.append(token)

    if not result.name and names:
        result.name = names[0]
    if not result.major and majors:
        result.major = majors[0]
