from __future__ import annotations

from typing import Literal

from admin.labels import applicant_summary
from core.pipeline import ReparseOutcome

ReparseMode = Literal["auto", "rule", "ai"]


def parse_reparse_args(
    arg1: str = "",
    arg2: str = "",
    arg3: str = "",
) -> dict | None:
    """Parse: <ref> [rule|ai|auto] preview|confirm"""
    ref = (arg1 or "").strip()
    a2 = (arg2 or "").strip().lower()
    a3 = (arg3 or "").strip().lower()
    if not ref:
        return None
    mode: ReparseMode = "auto"
    action = ""
    if a2 in {"preview", "confirm"}:
        action = a2
    elif a2 in {"rule", "ai", "auto"} and a3 in {"preview", "confirm"}:
        mode = a2  # type: ignore[assignment]
        action = a3
    else:
        return None
    return {"ref": ref, "mode": mode, "action": action}


def format_reparse_help() -> str:
    return "\n".join(
        [
            "手动重解析 pending（只更新本地，不调 QQ）",
            "",
            "/audit reparse <n> preview",
            "/audit reparse <n> confirm",
            "/audit reparse <n> rule|ai|auto preview",
            "/audit reparse <n> rule|ai|auto confirm",
            "",
            "说明：",
            "- rule：只用规则 parser",
            "- ai：强制调用 AI（忽略旧 stored / 已尝试标记）",
            "- auto：按当前配置，但忽略旧 stored parsed",
            "- confirm 不执行同意/拒绝，需再 /audit ok 或 release",
        ]
    )


def _field(parsed: dict | None, *keys: str, default: str = "未提供") -> str:
    data = parsed or {}
    for key in keys:
        val = data.get(key)
        if val:
            return str(val)
    return default


def format_reparse_preview(
    outcome: ReparseOutcome,
    *,
    index: int | None,
    group_label: str | None = None,
    user_label: str | None = None,
) -> str:
    req = outcome.request
    if req is None:
        return outcome.message or "重解析失败。"
    idx = index if index is not None else "?"
    group_text = (group_label or "").strip() or f"群 {req.group_id}"
    qq_text = (user_label or "").strip() or req.user_id
    old_p = outcome.old_parsed or req.parsed or {}
    new_p = outcome.new_parsed or {}
    summary_src = req
    if new_p.get("name") or new_p.get("student_id") or new_p.get("major") or new_p.get(
        "major_text"
    ):

        class _SummaryProxy:
            parsed = new_p
            profile = getattr(req, "profile", None) or (
                "graduate" if new_p.get("admission_type") else "undergraduate"
            )

        summary_src = _SummaryProxy()
    lines = [
        f"重解析预览 [{idx}]",
        "",
        f"申请：{applicant_summary(summary_src)}",
        f"QQ：{qq_text}",
        f"群：{group_text}",
        "",
        "旧解析：",
        f"姓名：{_field(old_p, 'name')}",
        f"学号：{_field(old_p, 'student_id')}",
        f"专业：{_field(old_p, 'major', 'major_text')}",
        f"判断：{outcome.old_reason or '（无）'}",
        "",
        "新解析：",
        f"姓名：{_field(new_p, 'name')}",
        f"学号：{_field(new_p, 'student_id')}",
        f"专业：{_field(new_p, 'major', 'major_text')}",
        f"判断：{outcome.new_reason or '（无）'}",
        f"匹配强度：{outcome.new_strength}",
        f"解析来源：{outcome.mode}",
    ]
    if outcome.ai_invoked:
        lines.append("AI：已调用")
    lines.extend(
        [
            "",
            "应用：",
            f"/audit reparse {idx} {outcome.mode} confirm"
            if outcome.mode != "auto"
            else f"/audit reparse {idx} confirm",
        ]
    )
    return "\n".join(lines)


def format_reparse_result(
    outcome: ReparseOutcome,
    *,
    index: int | None,
) -> str:
    if not outcome.ok:
        return outcome.message or "重解析失败。"
    idx = index if index is not None else "?"
    return "\n".join(
        [
            f"重解析已更新 [{idx}]",
            "",
            f"旧判断：{outcome.old_reason or '（无）'}",
            f"新判断：{outcome.new_reason or '（无）'}",
            f"匹配强度：{outcome.new_strength}",
            f"解析来源：{outcome.mode}",
            "",
            "未执行 QQ 审批动作。",
            "后续操作：",
            f"/audit ok {idx}",
            "/audit release preview",
        ]
    )
