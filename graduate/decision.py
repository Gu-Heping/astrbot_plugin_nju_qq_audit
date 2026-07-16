from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.decision import DecisionResult
from graduate.matcher import GraduateMatchResult
from graduate.models import GraduateParsedApplication

Decision = Literal["approve", "manual_review", "reject", "ignored"]


@dataclass
class GraduateDecisionResult:
    decision: Decision
    confidence: float
    reason: str
    suggestion: str | None = None
    match_type: str | None = None
    matched_student_key: str | None = None
    should_auto_approve: bool = False


def make_graduate_decision(
    parsed: GraduateParsedApplication,
    match: GraduateMatchResult,
    *,
    is_target_group: bool,
) -> DecisionResult:
    """Return DecisionResult compatible with existing pipeline auto-approve path."""
    if not is_target_group:
        return DecisionResult(decision="ignored", confidence=0, reason="非目标群，忽略")

    can_approve = (
        match.strength == "strong"
        and match.candidate_count == 1
        and match.matched_student is not None
        and bool(parsed.name)
        and parsed.admission_type in {"硕士", "博士"}
        and bool(parsed.major_text or parsed.major_code_candidates)
    )
    if can_approve:
        result = DecisionResult(
            decision="approve",
            confidence=match.confidence,
            reason=match.reason,
            suggestion="研究生强匹配，可自动通过（需 MODE=auto）",
            matched_student_key=match.matched_student_key,
            match_type="strong",
        )
        return result

    return DecisionResult(
        decision="manual_review",
        confidence=match.confidence,
        reason=match.reason or "研究生申请需人工复核",
        suggestion="请核实姓名、硕/博与专业",
        matched_student_key=match.matched_student_key,
        match_type=match.strength,
    )


def apply_graduate_auto_approve_flag(
    result: DecisionResult, mode: str, match: GraduateMatchResult
) -> DecisionResult:
    result.should_auto_approve = (
        result.decision == "approve"
        and mode == "auto"
        and match.strength == "strong"
        and match.candidate_count == 1
    )
    return result
