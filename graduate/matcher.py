from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from core.normalize import normalize_name, names_match
from graduate.models import GraduateParsedApplication, GraduateStudent

MatchStrength = Literal["strong", "weak", "none"]

_MAJOR_NOISE = re.compile(
    r"(专业|方向|学科|门类|类别|类|（.*?）|\(.*?\)|【.*?】)",
)


def normalize_major_text(text: str | None) -> str:
    if not text:
        return ""
    value = text.strip().lower().replace(" ", "").replace("　", "")
    value = _MAJOR_NOISE.sub("", value)
    return value


def majors_fuzzy_match(a: str | None, b: str | None) -> bool:
    na = normalize_major_text(a)
    nb = normalize_major_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return False


def major_code_match(code: str | None, student: GraduateStudent) -> bool:
    if not code or not student.major_code:
        return False
    return str(code).strip() == str(student.major_code).strip()


@dataclass
class GraduateMatchResult:
    strength: MatchStrength
    confidence: float
    reason: str
    matched_student_key: str | None = None
    matched_student: GraduateStudent | None = None
    matched_by: list[str] = field(default_factory=list)
    candidate_count: int = 0


def match_graduate(
    parsed: GraduateParsedApplication,
    students: list[GraduateStudent],
) -> GraduateMatchResult:
    if not students:
        return GraduateMatchResult(
            strength="none",
            confidence=0,
            reason="研究生名单为空，请先 /audit sync-grad",
        )

    name = normalize_name(parsed.name) if parsed.name else None
    adm = parsed.admission_type  # already normalized 硕士/博士 or None
    major = parsed.major_text
    codes = list(parsed.major_code_candidates or [])

    # Filter by name first when present
    pool = students
    if name:
        pool = [s for s in students if names_match(name, s.name)]
        if not pool:
            return GraduateMatchResult(
                strength="none",
                confidence=0.1,
                reason="姓名未命中研究生名单",
            )

    # Further filter by admission type
    if adm:
        typed = [s for s in pool if s.admission_type == adm]
        if typed:
            pool = typed
        elif name:
            # Name hit but type mismatch → still report
            return GraduateMatchResult(
                strength="none",
                confidence=0.3,
                reason=f"姓名命中但录取类型不匹配（申请={adm}）",
                candidate_count=len(pool),
            )

    # Major / code evidence. It can enable auto-approve when it matches, but
    # graduate release is intentionally based on unique name + admission type.
    unique_codes = list(dict.fromkeys(str(c).strip() for c in codes if str(c).strip()))
    major_code_hits: list[GraduateStudent] = []
    if len(unique_codes) == 1:
        for s in pool:
            if any(major_code_match(c, s) for c in unique_codes):
                major_code_hits.append(s)
    major_hits: list[GraduateStudent] = list(major_code_hits)
    if major and not major_hits:
        major_hits = [s for s in pool if majors_fuzzy_match(major, s.major_name)]
    elif major and major_hits:
        # Both code and name supplied: require intersection. Code-only fallback
        # would auto-approve conflicting credentials (e.g. 010101 + 中国哲学).
        both = [s for s in major_hits if majors_fuzzy_match(major, s.major_name)]
        major_hits = both

    if (unique_codes or major) and major_hits:
        pool = major_hits

    # Decision ladder
    if not name and not adm and not major and not unique_codes:
        return GraduateMatchResult(
            strength="none",
            confidence=0,
            reason="无法解析申请信息",
        )

    if not name:
        return GraduateMatchResult(
            strength="none",
            confidence=0.2,
            reason="缺少姓名，无法确认身份",
            candidate_count=len(pool),
        )

    # Strong: name + admission_type, unique. Major/code only controls whether
    # this can auto-approve; mismatch still enters release with admin notice.
    if adm and len(pool) == 1:
        s = pool[0]
        matched_by = ["name", "admission_type"]
        code_matches = len(unique_codes) == 1 and any(
            major_code_match(c, s) for c in unique_codes
        )
        major_is_code_value = bool(major and major in unique_codes)
        major_matches = bool(
            major
            and not major_is_code_value
            and majors_fuzzy_match(major, s.major_name)
        )
        code_conflict = len(unique_codes) > 1
        mixed_conflict = bool(
            unique_codes
            and major
            and not major_is_code_value
            and not (code_matches and major_matches)
        )
        if not code_conflict and not mixed_conflict:
            if code_matches:
                matched_by.append("major_code")
            if major_matches:
                matched_by.append("major_name")
        has_major_input = bool(unique_codes or major)
        has_major_evidence = "major_code" in matched_by or "major_name" in matched_by
        if has_major_evidence:
            confidence = 0.95
            reason = "姓名+录取类型+专业强匹配（唯一）"
        elif has_major_input:
            confidence = 0.75
            reason = "姓名+录取类型强匹配（唯一，专业/代码未匹配，以名单为准，默认进入release，管理员可提前拒绝）"
        else:
            confidence = 0.8
            reason = "姓名+录取类型强匹配（唯一，专业以名单为准，默认进入release，管理员可提前拒绝）"
        return GraduateMatchResult(
            strength="strong",
            confidence=confidence,
            reason=reason,
            matched_student_key=s.key,
            matched_student=s,
            matched_by=matched_by,
            candidate_count=1,
        )

    if len(pool) > 1:
        return GraduateMatchResult(
            strength="weak",
            confidence=0.5,
            reason=f"多候选（{len(pool)}），需人工复核",
            candidate_count=len(pool),
        )

    if len(pool) == 1:
        s = pool[0]
        if (major or unique_codes) and not adm:
            code_matches = len(unique_codes) == 1 and any(
                major_code_match(c, s) for c in unique_codes
            )
            major_is_code_value = bool(major and major in unique_codes)
            major_matches = bool(
                major
                and not major_is_code_value
                and majors_fuzzy_match(major, s.major_name)
            )
            matched_by = ["name"]
            if code_matches:
                matched_by.append("major_code")
            if major_matches:
                matched_by.append("major_name")
            if code_matches or major_matches:
                return GraduateMatchResult(
                    strength="strong",
                    confidence=0.85,
                    reason="姓名+专业唯一，但未提供硕/博，默认进入release，管理员可提前拒绝",
                    matched_student_key=s.key,
                    matched_student=s,
                    matched_by=matched_by,
                    candidate_count=1,
                )
            return GraduateMatchResult(
                strength="weak",
                confidence=0.45,
                reason="姓名唯一但专业/代码未命中，且未提供硕/博",
                matched_student_key=s.key,
                matched_student=s,
                matched_by=["name"],
                candidate_count=1,
            )
        return GraduateMatchResult(
            strength="weak",
            confidence=0.4,
            reason="姓名唯一但信息不足",
            matched_student_key=s.key,
            matched_student=s,
            matched_by=["name"],
            candidate_count=1,
        )

    return GraduateMatchResult(
        strength="none",
        confidence=0.1,
        reason="无强匹配，需人工复核",
        candidate_count=0,
    )
