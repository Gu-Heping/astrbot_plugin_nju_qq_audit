from __future__ import annotations

import re

from core.normalize import normalize_name, normalize_whitespace
from core.parser import extract_answer_segment
from graduate.models import GraduateParsedApplication

CHINESE_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")
MAJOR_CODE_PATTERN = re.compile(r"^\d{4,8}$")
NAME_LABEL_PATTERN = re.compile(
    # Non-greedy so 「姓名：张三录取专业…」 stops at 张三, not 张三录取.
    r"(?:姓名|名字)[:：\s]+([\u4e00-\u9fa5·]{2,4}?)"
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
    r"([\u4e00-\u9fa5a-zA-Z0-9（）()·\-]{2,40}?)"
    r"(?=\s|类型|录取类型|学位类型|学段|专业代码|$|[，,；;|/／]|姓名|名字)",
    re.IGNORECASE,
)
TYPE_LABEL_PATTERN = re.compile(
    # Do not stop at 「/」— otherwise 「类型：硕/博」 captures only 「硕」.
    r"(?:类型|录取类型|学位类型|学段)[:：\s]*"
    r"([^\s,，；;]{1,12}?)"
    r"(?=\s|专业|姓名|名字|$|[，,；;])",
    re.IGNORECASE,
)

_LEADING_MAJOR_CODE = re.compile(
    r"^(\d{4,8})([\u4e00-\u9fa5a-zA-Z（）()·\-].*)$"
)
_TRAILING_MAJOR_CODE = re.compile(
    r"^([\u4e00-\u9fa5a-zA-Z（）()·\-].*?)(\d{4,8})$"
)

# Template placeholders that mean "pick one", not a concrete admission type.
_AMBIGUOUS_TYPE_PATTERN = re.compile(
    r"(硕\s*[/／或]\s*博|博\s*[/／或]\s*硕|"
    r"硕士\s*[/／或]\s*博士|博士\s*[/／或]\s*硕士|硕博)",
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
    # Ambiguous template placeholders (硕/博) must not resolve to a concrete type.
    if raw == "硕博" or (
        any(sep in raw for sep in ("/", "／", "或"))
        and ("硕" in raw or "master" in raw)
        and ("博" in raw or "doctor" in raw or "phd" in raw)
    ):
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


def _absorb_major_value(result: GraduateParsedApplication, raw: str) -> None:
    """Store labeled/free major text; peel embedded 4–8 digit codes when present."""
    value = normalize_whitespace(raw)
    if not value:
        return
    if MAJOR_CODE_PATTERN.fullmatch(value):
        if value not in result.major_code_candidates:
            result.major_code_candidates.append(value)
        return
    leading = _LEADING_MAJOR_CODE.match(value)
    if leading:
        code, rest = leading.group(1), normalize_whitespace(leading.group(2))
        if code not in result.major_code_candidates:
            result.major_code_candidates.append(code)
        if rest and not result.major_text:
            result.major_text = rest
        return
    trailing = _TRAILING_MAJOR_CODE.match(value)
    if trailing:
        rest, code = normalize_whitespace(trailing.group(1)), trailing.group(2)
        if code not in result.major_code_candidates:
            result.major_code_candidates.append(code)
        if rest and not result.major_text:
            result.major_text = rest
        return
    if not result.major_text:
        result.major_text = value


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
        _absorb_major_value(result, major_label.group(1))

    type_label = TYPE_LABEL_PATTERN.search(text)
    if type_label:
        result.admission_type_raw = type_label.group(1).strip()
        result.admission_type = normalize_admission_type(result.admission_type_raw)

    # Protect 「硕/博」 placeholders before splitting on 「/」.
    text_for_tokens = _AMBIGUOUS_TYPE_PATTERN.sub(" ", text)
    cleaned = re.sub(r"[,，；;|/／]+", " ", text_for_tokens)
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

    # If free text contains both 硕 and 博 signals, treat type as unresolved.
    free_types = {
        normalize_admission_type(t)
        for t in free
        if normalize_admission_type(t) is not None
    }
    free_type_ambiguous = len(free_types) > 1

    for t in free:
        if MAJOR_CODE_PATTERN.fullmatch(t):
            if t not in result.major_code_candidates:
                result.major_code_candidates.append(t)
            continue
        adm = normalize_admission_type(t)
        if adm and not result.admission_type and not free_type_ambiguous:
            result.admission_type = adm
            result.admission_type_raw = t
            continue
        if free_type_ambiguous and adm:
            # Keep raw clue but do not pick a side.
            if not result.admission_type_raw:
                result.admission_type_raw = "硕/博"
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
            _absorb_major_value(result, t)
            continue
        # Second Chinese name-like after name already set → likely major fragment
        if result.name and not result.major_text and len(t) >= 2 and re.search(r"[\u4e00-\u9fa5]", t):
            if normalize_admission_type(t) is None and t not in _UNKNOWN_TYPE_TOKENS:
                _absorb_major_value(result, t)

    if not result.name and not result.major_text and not result.admission_type and not result.major_code_candidates:
        result.parse_errors.append("unable to parse graduate fields")

    return result
