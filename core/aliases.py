from __future__ import annotations

from difflib import SequenceMatcher

from core.normalize import normalize_major

MAJOR_ALIASES: dict[str, list[str]] = {
    "计算机科学与技术": ["计算机科学与技术", "计算机类", "计算机", "计科"],
    "计算机科学与技术(至诚班)": ["计算机科学与技术(至诚班)", "计算机至诚班"],
    "软件工程": ["软件工程", "软件工程(智能化软件)", "软件", "软工"],
    "人工智能": ["人工智能", "AI", "ai"],
    "电子信息类": ["电子信息类", "电子信息", "电信", "电子", "通信", "通信工程"],
    "集成电路设计与集成系统": [
        "集成电路设计与集成系统",
        "集成电路设计与集成系统(至诚班)",
        "集成电路",
        "IC",
        "微电子",
    ],
    "工科试验班": ["工科试验班", "工科", "自动化", "自动化类", "电气", "电气工程及其自动化"],
    "理科试验班": [
        "理科试验班",
        "理科试验班(匡亚明学院大理科班)",
        "理科试验班类(数理科学类)",
        "理科班",
    ],
    "建筑学": ["建筑学", "建筑"],
    "城乡规划": ["城乡规划", "城规"],
    "经济管理试验班": ["经济管理试验班(数智经济与管理)", "经济管理试验班", "经管试验班"],
    "物联网工程": ["物联网", "物联网工程"],
    "马克思主义理论": ["马克思主义理论", "马理论", "马克思主义"],
}

_alias_to_canonical: dict[str, str] = {}
for canonical, aliases in MAJOR_ALIASES.items():
    for alias in aliases:
        _alias_to_canonical[alias.lower()] = canonical
    _alias_to_canonical[canonical.lower()] = canonical


def get_canonical_major(major: str) -> str:
    normalized = major.strip()
    return _alias_to_canonical.get(normalized.lower(), normalized)


def is_known_major_token(token: str) -> bool:
    trimmed = token.strip()
    if not trimmed:
        return False
    norm = normalize_major(trimmed).lower()
    if norm in _alias_to_canonical:
        return True
    return trimmed.lower() in _alias_to_canonical


def build_major_index(students) -> set[str]:
    majors: set[str] = set()
    for student in students:
        if student.major:
            majors.add(student.major)
            majors.add(get_canonical_major(student.major))
            majors.add(normalize_major(student.major))
    return {m for m in majors if m}


def majors_match(a: str, b: str) -> bool:
    return majors_match_fuzzy(a, b, None)


def majors_match_fuzzy(a: str, b: str, known_majors: set[str] | None) -> bool:
    canon_a = get_canonical_major(a)
    canon_b = get_canonical_major(b)
    if canon_a == canon_b:
        return True
    if a == b:
        return True

    norm_a = normalize_major(a)
    norm_b = normalize_major(b)
    if norm_a == norm_b:
        return True
    if norm_a and norm_b and (norm_a in norm_b or norm_b in norm_a):
        return True

    short, long = (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)
    if len(short) >= 2 and short in long:
        return True

    if known_majors:
        for known in known_majors:
            kn = normalize_major(known)
            if majors_match_fuzzy(a, known, None) or majors_match_fuzzy(b, known, None):
                if normalize_major(a) == kn or normalize_major(b) == kn:
                    return True

    ratio = SequenceMatcher(None, norm_a.lower(), norm_b.lower()).ratio()
    if ratio >= 0.85 and min(len(norm_a), len(norm_b)) >= 2:
        return True
    return False
