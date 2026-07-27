from __future__ import annotations

from dataclasses import dataclass

from config import PluginSettings, get_effective_undergrad_exclusive_action
from storage.runtime_store import RuntimeStore

EXCLUSIVE_ACTION_LABELS = {
    "manual_review": "转人工",
    "auto_reject": "自动拒绝",
}

EXCLUSIVE_ACTION_SOURCE_LABELS = {
    "runtime": "运行时指令",
    "plugin_config": "插件配置",
    "default": "默认值",
}


@dataclass(frozen=True)
class PolicyCommand:
    kind: str
    confirmed: bool = False


def parse_policy_command(
    message_str: str,
    arg1: str = "",
    arg2: str = "",
    arg3: str = "",
    arg4: str = "",
) -> PolicyCommand:
    parts = [p for p in (message_str or "").strip().split() if p]
    try:
        idx = parts.index("policy")
        tokens = [t.lower() for t in parts[idx + 1 :]]
    except ValueError:
        tokens = [
            t.lower()
            for t in (arg1, arg2, arg3, arg4)
            if (t or "").strip()
        ]

    if not tokens or tokens[0] in {"status", "状态"}:
        return PolicyCommand(kind="status")

    if tokens[0] != "exclusive":
        return PolicyCommand(kind="unknown")

    if len(tokens) < 2:
        return PolicyCommand(kind="unknown")

    mode = tokens[1]
    confirmed = len(tokens) >= 3 and tokens[2] == "confirm"

    if mode == "manual":
        return PolicyCommand(kind="switch_manual", confirmed=confirmed)
    if mode in {"auto-reject", "auto", "reject"}:
        return PolicyCommand(kind="switch_auto_reject", confirmed=confirmed)
    return PolicyCommand(kind="unknown")


def format_policy_status(settings: PluginSettings, runtime: RuntimeStore) -> str:
    override = runtime.get_undergrad_exclusive_action_override()
    action, source = get_effective_undergrad_exclusive_action(settings, override)
    action_label = EXCLUSIVE_ACTION_LABELS.get(action, action)
    source_label = EXCLUSIVE_ACTION_SOURCE_LABELS.get(source, source)
    source_group = (
        (settings.undergrad_overflow_source_group_id or "").strip() or "未配置"
    )
    redirect_group = (
        (settings.undergrad_overflow_redirect_group_id or "").strip() or "未配置"
    )
    lines = [
        "本科路由策略",
        "",
        f"多群互斥：{'开启' if settings.undergrad_exclusive_groups_enabled else '关闭'}",
        f"当前处理方式：{action_label}",
        f"处理方式来源：{source_label}",
        f"满员引导：{'开启' if settings.undergrad_overflow_enabled else '关闭'}",
        f"满员阈值：{settings.undergrad_overflow_threshold}",
        f"源群：{source_group}",
        f"备用群：{redirect_group}",
        "",
        "说明：",
        "- 转人工：已在其他本科目标群的 QQ 再申请本科目标群时，只提醒管理员人工处理。",
        "- 自动拒绝：已在其他本科目标群的 QQ 再申请本科目标群时，实时申请会自动拒绝。",
        "- release/catchup 不会批量拒绝，只会跳过并转人工确认。",
        "",
        "切换：",
        "/audit policy exclusive manual confirm",
        "/audit policy exclusive auto-reject confirm",
    ]
    if settings.undergrad_exclusive_groups_enabled and action == "auto_reject":
        lines.insert(5, "⚠️ 当前为自动拒绝：实时新申请命中多群互斥时会自动拒绝。")
    return "\n".join(lines)


def format_policy_unknown() -> str:
    return "\n".join(
        [
            "未知策略指令。",
            "",
            "查看：/audit policy",
            "切换：/audit policy exclusive manual confirm",
            "      /audit policy exclusive auto-reject confirm",
            "帮助：/audit help policy",
        ]
    )


def format_exclusive_manual_confirm_prompt() -> str:
    return "\n".join(
        [
            "此操作会改变本科多群互斥的实时处理方式。",
            "确认切换为转人工请发送：",
            "/audit policy exclusive manual confirm",
        ]
    )


def format_exclusive_auto_reject_confirm_prompt() -> str:
    return "\n".join(
        [
            "此操作会改变本科多群互斥的实时处理方式。",
            "确认切换为自动拒绝请发送：",
            "/audit policy exclusive auto-reject confirm",
        ]
    )


def format_switched_to_manual() -> str:
    return "\n".join(
        [
            "已切换本科多群互斥策略：转人工",
            "",
            "后续命中「已在其他本科目标群」的实时申请会提醒管理员人工处理，不会自动拒绝。",
            "release/catchup 仍不会批量拒绝。",
        ]
    )


def format_switched_to_auto_reject() -> str:
    return "\n".join(
        [
            "已切换本科多群互斥策略：自动拒绝 ⚠️",
            "",
            "后续命中「已在其他本科目标群」的实时申请会自动拒绝。",
            "release/catchup 不会批量拒绝，只会跳过并转人工确认。",
            "",
            "建议先用测试 QQ 验证拒绝理由符合运营预期。",
        ]
    )


async def handle_policy_command(
    *,
    settings: PluginSettings,
    runtime: RuntimeStore,
    command: PolicyCommand,
    updated_by: str,
) -> str:
    if command.kind == "status":
        return format_policy_status(settings, runtime)
    if command.kind == "switch_manual":
        if not command.confirmed:
            return format_exclusive_manual_confirm_prompt()
        await runtime.set_undergrad_exclusive_action_override(
            "manual_review", updated_by
        )
        return format_switched_to_manual()
    if command.kind == "switch_auto_reject":
        if not command.confirmed:
            return format_exclusive_auto_reject_confirm_prompt()
        await runtime.set_undergrad_exclusive_action_override(
            "auto_reject", updated_by
        )
        return format_switched_to_auto_reject()
    return format_policy_unknown()
