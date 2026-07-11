from __future__ import annotations

import re

NON_GRADE26_KEYWORDS = [
    "学长",
    "学姐",
    "住届",
    "往届",
    "25级",
    "24级",
    "23级",
    "22级",
    "21级",
    "20级",
    "研究生",
    "硕士",
    "博士",
    "家长",
    "非新生",
]

_FULLWIDTH_TRANS = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def to_halfwidth(text: str) -> str:
    return text.translate(_FULLWIDTH_TRANS)


_GRADE_PREFIX_ON_NAME = re.compile(r"^(?:202[56]|2[0-9])级?(?=[\u4e00-\u9fa5·])")


def normalize_name(name: str) -> str:
    value = normalize_whitespace(to_halfwidth(name)).replace(" ", "")
    value = _GRADE_PREFIX_ON_NAME.sub("", value)
    return value


def normalize_student_id(student_id: str) -> str:
    return re.sub(r"\D", "", to_halfwidth(student_id))


def get_grade_from_student_id(student_id: str) -> str | None:
    normalized = normalize_student_id(student_id)
    if len(normalized) < 2:
        return None
    return normalized[:2]


def is_grade26_student_id(student_id: str) -> bool:
    return get_grade_from_student_id(student_id) == "26"


def normalize_notice_no(notice_no: str) -> str:
    value = to_halfwidth(notice_no).strip()
    value = re.sub(r"[\s\-_/\\·]+", "", value)
    return value.upper()


def normalize_major(major: str) -> str:
    value = normalize_whitespace(to_halfwidth(major))
    value = re.sub(r"[（(][^）)]*[）)]", "", value)
    value = re.sub(r"方向$", "", value)
    value = re.sub(r"类$", "", value)
    value = re.sub(r"专业$", "", value)
    return value.strip()


def parse_qq_field(raw: str) -> str | None:
    if not raw:
        return None
    parts = re.split(r"[,，;；/|\s]+", str(raw).strip())
    for part in parts:
        digits = re.sub(r"\D", "", part)
        if len(digits) >= 5:
            return digits
    return None


def parse_qq_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,，;；/|\s]+", str(raw).strip())
    result: list[str] = []
    for part in parts:
        digits = re.sub(r"\D", "", part)
        if len(digits) >= 5:
            result.append(digits)
    return result


def student_qq_matches(user_id: str | None, student_qq: str | None) -> bool:
    if not user_id or not student_qq:
        return False
    uid = re.sub(r"\D", "", str(user_id))
    return uid in parse_qq_list(student_qq)


def has_non_grade26_keyword(text: str) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower or kw in text for kw in NON_GRADE26_KEYWORDS)


def names_match(a: str, b: str) -> bool:
    return normalize_name(a) == normalize_name(b)


def student_ids_match(a: str, b: str) -> bool:
    return normalize_student_id(a) == normalize_student_id(b)


def notice_nos_match(a: str, b: str) -> bool:
    return normalize_notice_no(a) == normalize_notice_no(b)


def looks_like_qq_token(token: str) -> bool:
    digits = re.sub(r"\D", "", token)
    if not digits or not token.isdigit():
        return False
    if len(digits) >= 8 and digits.startswith(("2025", "2026")):
        return False
    return 5 <= len(digits) <= 11
