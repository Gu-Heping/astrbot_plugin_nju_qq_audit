from __future__ import annotations

import re

from core.normalize import normalize_name, normalize_whitespace
from core.parser import extract_answer_segment
from graduate.models import GraduateParsedApplication

CHINESE_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")
MAJOR_CODE_PATTERN = re.compile(r"^\d{4,8}$")
NAME_LABEL_PATTERN = re.compile(
    # Stop before the next field label; do not use [:：] alone as a stop —
    # glued 「姓名：张三专业：…」 would otherwise greedily swallow 「张三专业」.
    r"(?:姓名|名字)[:：\s]+([\u4e00-\u9fa5·]{2,4})"
    r"(?=\s|专业|类型|录取|$|[，,；;|/／])",
    re.IGNORECASE,
)
# Prefer explicit 专业代码 so 「专业代码：010101」 is not eaten by 专业 label.
MAJOR_CODE_LABEL_PATTERN = re.compile(
    r"(?:专业代码|录取专业代码)[:：\s]*(\d{4,8})",
    re.IGNORECASE,
)
MAJOR_LABEL_PATTERN = re.compile(
    r"(?:专业名称|录取专业|报读专业|专业(?!代码))[:：\s]*"
    r"([\u4e00-\u9fa5a-zA-Z（）()·\-]{2,40})",
    re.IGNORECASE,
)
TYPE_LABEL_PATTERN = re.compile(
    r"(?:类型|录取类型|学位类型|学段)[:：\s]*([^\s,，；;]{1,12})",
    re.IGNORECASE,
)

_MASTER_TOKENS = frozenset(
    {
        "硕",
        "硕士",
        "专硕",
        "学硕",
        "master",
        "msc",
        "ma",
        "mba",
        "mpa",
    }
)
_DOCTOR_TOKENS = frozenset(
    {
        "博",
        "博士",
        "直博",
        "phd",
        "ph.d",
        "ph.d.",
        "doctor",
        "dr",
    }
)
_UNKNOWN_TYPE_TOKENS = frozenset({"研究生", "研", "graduate"})

_TEMPLATE_SKIP = frozenset(
    {
        "问题",
        "答案",
        "姓名",
        "专业",
        "硕博",
        "硕/博",
        "类型",
        "答",
        "回答",
    }
)


def normalize_admission_type(token: str | None) -> str | None:
    """Return 硕士 / 博士 / None. 「研究生」 stays None (unknown)."""
    if not token:
        return None
    raw = normalize_whitespace(token).lower().replace(" ", "")
    if not raw:
        return None
    if raw in _UNKNOWN_TYPE_TOKENS or raw == "研究生":
        return None
    # Strip common wrappers
    compact = raw.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    if compact in _MASTER_TOKENS or any(t in compact for t in ("硕士", "专硕", "学硕")):
        if compact in _DOCTOR_TOKENS or "博士" in compact or "phd" in compact:
            # Prefer explicit doctor if both somehow present
            if "博士" in compact or "phd" in compact or compact in _DOCTOR_TOKENS:
                return "博士"
        return "硕士"
    if compact in _DOCTOR_TOKENS or "博士" in compact or "phd" in compact or "直博" in compact:
        return "博士"
    # Single-char 硕/博
    if compact == "硕":
        return "硕士"
    if compact == "博":
        return "博士"
    return None


def _looks_like_name(token: str) -> bool:
    return bool(CHINESE_NAME_PATTERN.fullmatch(token))


def _looks_like_major(token: str) -> bool:
    if not token or len(token) < 2:
        return False
    if normalize_admission_type(token) is not None:
        return False
    if token in _UNKNOWN_TYPE_TOKENS or token == "研究生":
        return False
    if MAJOR_CODE_PATTERN.fullmatch(token):
        return False
    if _looks_like_name(token) and len(token) <= 4:
        # Short Chinese person name — not major unless labeled
        return False
    return True


def parse_graduate_comment(raw: str) -> GraduateParsedApplication:
    full = normalize_whitespace(raw)
    text = normalize_whitespace(extract_answer_segment(raw))
    result = GraduateParsedApplication(raw=full)
    if not text:
        result.parse_errors.append("empty comment")
        return result

    name_label = NAME_LABEL_PATTERN.search(text)
    if name_label:
        result.name = normalize_name(name_label.group(1))

    major_code_label = MAJOR_CODE_LABEL_PATTERN.search(text)
    if major_code_label:
        code = major_code_label.group(1)
        if code not in result.major_code_candidates:
            result.major_code_candidates.append(code)

    major_label = MAJOR_LABEL_PATTERN.search(text)
    if major_label:
        result.major_text = normalize_whitespace(major_label.group(1))

    type_label = TYPE_LABEL_PATTERN.search(text)
    if type_label:
        result.admission_type_raw = type_label.group(1).strip()
        result.admission_type = normalize_admission_type(result.admission_type_raw)

    # Tokenize remaining free text (drop punctuation)
    cleaned = re.sub(r"[,，；;|/／]+", " ", text)
    tokens = [t for t in cleaned.split() if t and t.lower() not in _TEMPLATE_SKIP]

    # Strip labeled values already consumed from free tokens where exact match
    free: list[str] = []
    for t in tokens:
        if result.name and normalize_name(t) == result.name:
            continue
        if result.major_text and normalize_whitespace(t) == result.major_text:
            continue
        if result.admission_type_raw and t == result.admission_type_raw:
            continue
        # Skip label prefixes like 姓名：刘尚明 already partially handled
        if "：" in t or ":" in t:
            continue
        free.append(t)

    for t in free:
        if MAJOR_CODE_PATTERN.fullmatch(t):
            if t not in result.major_code_candidates:
                result.major_code_candidates.append(t)
            continue
        adm = normalize_admission_type(t)
        if adm and not result.admission_type:
            result.admission_type = adm
            result.admission_type_raw = t
            continue
        if t in _UNKNOWN_TYPE_TOKENS or t == "研究生":
            # Explicitly do not force master
            if not result.admission_type_raw:
                result.admission_type_raw = t
            continue
        if not result.name and _looks_like_name(t):
            result.name = normalize_name(t)
            continue
        if not result.major_text and _looks_like_major(t):
            result.major_text = normalize_whitespace(t)
            continue
        # Second Chinese name-like after name already set → likely major fragment
        if result.name and not result.major_text and len(t) >= 2 and re.search(r"[\u4e00-\u9fa5]", t):
            if normalize_admission_type(t) is None and t not in _UNKNOWN_TYPE_TOKENS:
                result.major_text = normalize_whitespace(t)

    if not result.name and not result.major_text and not result.admission_type and not result.major_code_candidates:
        result.parse_errors.append("unable to parse graduate fields")

    return result
