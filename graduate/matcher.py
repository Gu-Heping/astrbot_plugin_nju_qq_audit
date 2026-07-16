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

    # Major / code filter
    major_hits: list[GraduateStudent] = []
    if codes:
        for s in pool:
            if any(major_code_match(c, s) for c in codes):
                major_hits.append(s)
    if major and not major_hits:
        major_hits = [s for s in pool if majors_fuzzy_match(major, s.major_name)]
    elif major and major_hits:
        # Both code and name supplied: require intersection. Code-only fallback
        # would auto-approve conflicting credentials (e.g. 010101 + 中国哲学).
        both = [s for s in major_hits if majors_fuzzy_match(major, s.major_name)]
        major_hits = both

    if codes or major:
        if not major_hits:
            if name and adm:
                return GraduateMatchResult(
                    strength="none",
                    confidence=0.35,
                    reason="姓名+录取类型命中但专业/代码不匹配",
                    candidate_count=len(pool),
                )
            if name:
                return GraduateMatchResult(
                    strength="none",
                    confidence=0.3,
                    reason="姓名命中但专业/代码不匹配",
                    candidate_count=len(pool),
                )
            return GraduateMatchResult(
                strength="none",
                confidence=0.2,
                reason="专业/代码有线索但无有效命中",
            )
        pool = major_hits

    # Decision ladder
    if not name and not adm and not major and not codes:
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

    # Strong: name + admission_type + major/code, unique
    if adm and (major or codes) and len(pool) == 1:
        s = pool[0]
        matched_by = ["name", "admission_type"]
        if codes and any(major_code_match(c, s) for c in codes):
            matched_by.append("major_code")
        if major and majors_fuzzy_match(major, s.major_name):
            matched_by.append("major_name")
        return GraduateMatchResult(
            strength="strong",
            confidence=0.95,
            reason="姓名+录取类型+专业强匹配（唯一）",
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
        if adm and not (major or codes):
            return GraduateMatchResult(
                strength="weak",
                confidence=0.55,
                reason="姓名+录取类型唯一，但未提供专业",
                matched_student_key=s.key,
                matched_student=s,
                matched_by=["name", "admission_type"],
                candidate_count=1,
            )
        if (major or codes) and not adm:
            return GraduateMatchResult(
                strength="weak",
                confidence=0.55,
                reason="姓名+专业唯一，但未提供硕/博",
                matched_student_key=s.key,
                matched_student=s,
                matched_by=["name", "major"],
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
