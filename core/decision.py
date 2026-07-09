from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.matcher import MatchResult, is_non_grade26
from core.normalize import has_non_grade26_keyword
from core.parser import ParsedApplication

Decision = Literal["approve", "manual_review", "reject", "ignored"]


@dataclass
class DecisionResult:
    decision: Decision
    confidence: float
    reason: str
    suggestion: str | None = None
    match_type: str | None = None
    matched_student_key: str | None = None
    should_auto_approve: bool = False


def make_decision(
    parsed: ParsedApplication,
    match: MatchResult,
    *,
    is_target_group: bool,
) -> DecisionResult:
    if not is_target_group:
        return DecisionResult(decision="ignored", confidence=0, reason="非目标群，忽略")

    if has_non_grade26_keyword(parsed.raw):
        return DecisionResult(
            decision="manual_review",
            confidence=0,
            reason="申请文本含非26级关键词（学长/学姐/住届等），需人工复核",
            suggestion="请确认是否为26级新生",
        )

    if is_non_grade26(match, parsed):
        return DecisionResult(
            decision="manual_review",
            confidence=match.confidence,
            reason="学号非26级（前两位非26），需人工复核",
            suggestion="可能是往届学长学姐，请勿自动通过",
            matched_student_key=match.matched_student_key,
            match_type=match.strength,
        )

    if match.strength == "strong":
        return DecisionResult(
            decision="approve",
            confidence=match.confidence,
            reason=match.reason,
            suggestion="强匹配，可自动通过（需 MODE=auto）",
            matched_student_key=match.matched_student_key,
            match_type="strong",
        )

    if match.strength == "weak":
        return DecisionResult(
            decision="manual_review",
            confidence=match.confidence,
            reason=match.reason,
            suggestion="姓名+专业/书院弱匹配，请人工核实学号或通知书编号",
            matched_student_key=match.matched_student_key,
            match_type="weak",
        )

    if match.strength == "auxiliary":
        return DecisionResult(
            decision="manual_review",
            confidence=match.confidence,
            reason=match.reason,
            suggestion="QQ辅助匹配，请人工核实",
            matched_student_key=match.matched_student_key,
            match_type="auxiliary",
        )

    if not parsed.name and not parsed.student_id and not parsed.notice_no and not parsed.major:
        return DecisionResult(
            decision="manual_review",
            confidence=0,
            reason="无法解析申请信息",
            suggestion="请联系申请人补充姓名+学号或姓名+通知书编号",
        )

    if parsed.name and not parsed.student_id and not parsed.notice_no and not parsed.major:
        return DecisionResult(
            decision="manual_review",
            confidence=match.confidence,
            reason="仅姓名，信息不足",
            suggestion="请核实学号或通知书编号",
            matched_student_key=match.matched_student_key,
        )

    if parsed.major and not parsed.name:
        return DecisionResult(
            decision="manual_review",
            confidence=0,
            reason="仅专业，无法确认身份",
            suggestion="请核实姓名+学号",
        )

    return DecisionResult(
        decision="manual_review",
        confidence=match.confidence,
        reason=match.reason or "无强匹配，需人工复核",
        suggestion="请人工审核",
        matched_student_key=match.matched_student_key,
        match_type=match.strength,
    )


def should_auto_approve(decision: Decision, mode: str, match: MatchResult) -> bool:
    return (
        decision == "approve"
        and mode == "auto"
        and match.strength == "strong"
    )


def apply_auto_approve_flag(result: DecisionResult, mode: str, match: MatchResult) -> DecisionResult:
    result.should_auto_approve = should_auto_approve(result.decision, mode, match)
    return result
