from __future__ import annotations

MODE_LABELS = {
    "record-only": "只记录，不自动放人",
    "manual": "人工审核",
    "auto": "自动审核强匹配",
    "off": "暂停处理",
}

DECISION_LABELS = {
    "approve": "建议通过",
    "manual_review": "需要人工确认",
    "reject": "建议拒绝",
    "ignored": "已忽略",
}

STRENGTH_SUMMARY = {
    "strong": "强匹配",
    "weak": "弱匹配",
    "auxiliary": "辅助匹配",
    "none": "未匹配",
}

DEFAULT_REJECT_REASON = "请填写真实姓名和学号后重新申请。"


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)


def decision_label(decision: str) -> str:
    return DECISION_LABELS.get(decision, decision)


def human_judgement(item) -> str:
    strength = getattr(item, "match_strength", None) or item.match.get("strength", "none")
    decision = getattr(item, "decision", "")
    reason = getattr(item, "reason", "") or ""
    strength_text = STRENGTH_SUMMARY.get(strength, strength)

    if decision == "approve" and strength == "strong":
        return f"强匹配，建议通过"
    if decision == "manual_review" and strength == "weak":
        return f"弱匹配，需要人工确认"
    if decision == "manual_review" and not reason:
        return "信息不足，需要人工确认"
    if reason:
        return reason
    return decision_label(decision) if decision else strength_text


def list_action_hint(item) -> str:
    decision = getattr(item, "decision", "")
    strength = getattr(item, "match_strength", None) or item.match.get("strength", "none")
    if decision == "approve" and strength == "strong":
        return "操作：/audit ok 编号"
    if decision == "manual_review":
        return "操作：/audit view 编号"
    return "操作：/audit view 编号"


def applicant_summary(item) -> str:
    parsed = getattr(item, "parsed", {}) or {}
    name = parsed.get("name") or "未识别"
    profile = getattr(item, "profile", None) or (getattr(item, "to_public_dict", lambda: {})() or {}).get("profile")
    if profile == "graduate" or parsed.get("admission_type"):
        major = parsed.get("major_text") or parsed.get("major")
        adm = parsed.get("admission_type")
        bits = [str(name)]
        if adm:
            bits.append(str(adm))
        if major:
            bits.append(str(major))
        return " / ".join(bits)
    student_id = parsed.get("student_id")
    notice_no = parsed.get("notice_no")
    major = parsed.get("major")
    if name and student_id:
        return f"{name} / {student_id}"
    if name and notice_no:
        return f"{name} / {notice_no}"
    if name and major:
        return f"{name} / {major}"
    return str(name)
