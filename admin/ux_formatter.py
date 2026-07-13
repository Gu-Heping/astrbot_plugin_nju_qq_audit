from __future__ import annotations

from datetime import datetime

from admin.formatter import admin_configured, format_status
from admin.labels import (
    DEFAULT_REJECT_REASON,
    applicant_summary,
    human_judgement,
    list_action_hint,
    mode_label,
)
from admin.command_resolver import sanitize_action_message
from config import PluginSettings, mask_http_url
from data_source.student_cache import SyncState


def _format_local_time(iso_text: str | None) -> str:
    if not iso_text:
        return "(无)"
    try:
        dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_text


def _adapter_status_text(adapter_probe: dict | None) -> str:
    probe = adapter_probe or {}
    available = probe.get("adapter_action_available", "unknown")
    if available == "yes":
        return "AstrBot adapter 可用"
    if available == "no":
        return "不可用"
    return "未知"


def _home_health(settings: PluginSettings, adapter_probe: dict | None) -> str:
    if not settings.target_group_ids or not admin_configured(settings):
        return "需要配置"
    available = (adapter_probe or {}).get("adapter_action_available", "unknown")
    if available == "no":
        return "有告警"
    return "正常"


def format_home(
    settings: PluginSettings,
    *,
    effective_mode: str,
    student_count: int,
    pending_count: int,
    sync_state: SyncState,
    adapter_probe: dict | None = None,
    releasable_count: int = 0,
    release_running: bool = False,
) -> str:
    lines: list[str] = []
    health = _home_health(settings, adapter_probe)

    if not settings.target_group_ids:
        lines.extend(
            [
                "⚠️ 未配置目标群",
                "当前不会处理任何入群申请。",
                "请到 AstrBot 插件配置里填写 target_group_ids。",
                "",
            ]
        )
    if not admin_configured(settings):
        lines.extend(
            [
                "⚠️ 未配置管理员",
                "除 status/probe 外，管理命令不可用。",
                "请到 AstrBot 插件配置里填写 admin_qq_ids。",
                "",
            ]
        )
    available = (adapter_probe or {}).get("adapter_action_available", "unknown")
    if available == "no":
        lines.extend(
            [
                "⚠️ 审批接口不可用",
                "可以先 record-only 记录申请，但无法自动同意/拒绝。",
                "请运行：/audit probe api",
                "",
            ]
        )

    lines.extend(
        [
            "NJU QQ Audit",
            "",
            f"状态：{health}",
            f"模式：{effective_mode}（{mode_label(effective_mode)}）",
            f"目标群：{', '.join(sorted(settings.target_group_ids)) or '(未配置)'}",
            f"学生数据：{settings.student_source}，{student_count} 人",
            f"待处理：{pending_count} 条",
            f"可分批通过：{releasable_count} 条",
            f"分批任务：{'进行中' if release_running else '空闲'}",
            f"最近同步：{_format_local_time(sync_state.last_sync_at)}，"
            f"{sync_state.last_sync_result or '(无)'}",
            f"审批接口：{_adapter_status_text(adapter_probe)}",
            "",
            "下一步：",
            "- 看待处理：/audit list",
            "- 分批通过：/audit release preview",
            "- 同步学生：/audit sync",
            "- 复盘报告：/audit report",
        ]
    )
    return "\n".join(lines)


def format_list(
    items: list,
    index_map: dict[int, str],
    *,
    reconcile_summary=None,
) -> str:
    if not items:
        body = "目前没有待处理申请。"
    else:
        lines = [f"待处理申请：{len(items)} 条", ""]
        for idx, item in enumerate(items, start=1):
            public = item.to_public_dict()
            summary = applicant_summary(item)
            comment = (public.get("comment") or "")[:80]
            lines.extend(
                [
                    f"[{idx}] {summary}",
                    f"群：{public.get('group_id', '')}",
                    f"验证：{comment or '（空）'}",
                    f"判断：{human_judgement(item)}",
                ]
            )
            last_action = public.get("last_action_result") or {}
            if last_action and last_action.get("ok") is False:
                lines.append("提示：上次操作失败，可重试或到 QQ 侧确认。")
            lines.append(list_action_hint(item).replace("编号", str(idx)))
            lines.append("")
        lines.append("编号来自本次列表，30 分钟内有效。无需复制长 request id。")
        body = "\n".join(lines)

    if reconcile_summary is not None:
        extra = reconcile_summary.to_display_lines()
        return body + "\n\n" + "\n".join(extra)
    return body


def _parsed_line(label: str, value) -> str:
    if value is None or value == "":
        return f"{label}：未提供"
    return f"{label}：{value}"


def format_view(item, index: int | None = None) -> str:
    public = item.to_public_dict()
    parsed = public.get("parsed") or {}
    status = public.get("status", "")
    title = f"申请详情 [{index}]" if index is not None else f"申请详情 {public.get('id', '')}"
    lines = [
        title,
        "",
        f"用户：{public.get('user_id', '')}",
        f"群：{public.get('group_id', '')}",
        f"验证：{public.get('comment', '')[:120]}",
        f"时间：{_format_local_time(public.get('created_at'))}",
        f"状态：{status}",
    ]
    revision = int(public.get("comment_revision") or 0)
    if revision > 0:
        lines.append(f"历史填写：{revision} 次")
    lines.extend(
        [
            "",
            "解析结果：",
            _parsed_line("姓名", parsed.get("name")),
            _parsed_line("学号", parsed.get("student_id")),
            _parsed_line("通知书编号", parsed.get("notice_no")),
            _parsed_line("专业", parsed.get("major")),
            _parsed_line("书院", parsed.get("academy")),
            "",
            "判断：",
            f"结果：{human_judgement(item)}",
            f"原因：{public.get('reason') or human_judgement(item)}",
            "",
        ]
    )
    last_action = public.get("last_action_result") or {}
    action_result = public.get("action_result") or {}
    if status == "external":
        lines.append("已在 QQ 客户端处理（external）。")
        msg = sanitize_action_message((action_result or last_action).get("message"))
        if msg and msg != "（无详情）":
            lines.append(f"说明：{msg}")
    elif status == "stale":
        lines.append("QQ 侧已无此申请（stale），无法确认是否已入群。")
        msg = sanitize_action_message((action_result or last_action).get("message"))
        if msg and msg != "（无详情）":
            lines.append(f"原因：{msg}")
        lines.append("请到 QQ 群管理后台确认；可 restore 恢复 pending 或 mark-external 确认已入群。")
    elif last_action:
        if last_action.get("ok"):
            lines.append("上次审批结果：成功")
        else:
            lines.append("上次审批结果：失败")
            lines.append(f"上次失败原因：{sanitize_action_message(last_action.get('message'))}")
            if index is not None:
                lines.append(
                    "重试建议：可再次 /audit ok/no；若 QQ 侧已处理请 "
                    f"/audit mark-external {index} confirm"
                )
            else:
                lines.append("重试建议：可再次操作或使用 mark-external confirm")
    qq_match = (public.get("match") or {}).get("qq_match")
    if qq_match is True:
        lines.append("QQ 匹配：是")
    elif qq_match is False:
        lines.append("QQ 匹配：否")
    else:
        lines.append("QQ 匹配：未记录")
    lines.append("可操作：")
    if status == "external":
        lines.append("（已在 QQ 侧处理，无需 ok/no）")
    elif status == "stale":
        if index is not None:
            lines.append(f"/audit restore {index} confirm")
            lines.append(f"/audit mark-external {index} confirm")
        else:
            lines.append(f"/audit restore {public.get('id')} confirm")
            lines.append(f"/audit mark-external {public.get('id')} confirm")
    elif status == "processed":
        lines.append("（已处理完成）")
    elif index is not None:
        lines.append(f"/audit ok {index}")
        lines.append(f"/audit no {index} 信息不完整")
        if last_action and last_action.get("ok") is False:
            lines.append(f"/audit mark-external {index} confirm")
    else:
        lines.append(f"/audit approve {public.get('id')} confirm")
        lines.append(f"/audit reject {public.get('id')} confirm")
    return "\n".join(lines)


def format_ok_result(item, index: int | None = None) -> str:
    public = item.to_public_dict()
    label = f"[{index}]" if index is not None else public.get("id", "")
    return "\n".join(
        [
            f"已同意申请 {label}",
            "",
            f"用户：{public.get('user_id', '')}",
            f"群：{public.get('group_id', '')}",
            "原因：管理员手动通过",
            "状态：processed",
        ]
    )


def format_no_result(item, index: int | None, reason: str) -> str:
    public = item.to_public_dict()
    label = f"[{index}]" if index is not None else public.get("id", "")
    return "\n".join(
        [
            f"已拒绝申请 {label}",
            "",
            f"用户：{public.get('user_id', '')}",
            f"理由：{reason or DEFAULT_REJECT_REASON}",
            "状态：processed",
        ]
    )


def format_auto_warning() -> str:
    return "\n".join(
        [
            "切换到自动审核前请确认：",
            "",
            "- auto 只会自动通过姓名+学号或姓名+通知书编号的 26 级 strong match",
            "- 弱匹配、信息不足不会自动拒绝",
            "- 日常暂停自动放人请用 /audit record，不是 off",
            "- 建议先完成 /audit sync 并人工抽查几条",
            "",
            "确认切换请发送：",
            "/audit auto confirm",
        ]
    )


def format_off_warning() -> str:
    return "\n".join(
        [
            "off 会完全跳过入群申请，不会记录 pending。",
            "",
            "日常暂停自动审核建议使用：",
            "/audit record",
            "",
            "确认完全停用请输入：",
            "/audit off confirm",
        ]
    )


def format_mode_changed(mode: str) -> str:
    base = f"运行模式已切换为：{mode}（{mode_label(mode)}）"
    if mode == "record-only":
        return base + "\n\n继续记录申请，但不会自动放人。之后可用 /audit release 10 confirm 分批通过强匹配申请。"
    return base


def format_manual_review_notice(
    *,
    index: int | None,
    group_id: str,
    user_id: str,
    comment: str,
    judgement: str,
) -> str:
    lines = [
        "新的入群申请需要确认",
        "",
    ]
    if index is not None:
        lines.append(f"[{index}] 用户：{user_id}")
    else:
        lines.append(f"用户：{user_id}")
    lines.extend(
        [
            f"群：{group_id}",
            f"验证：{(comment or '')[:80]}",
            f"判断：{judgement}",
            "",
        ]
    )
    if index is not None:
        lines.extend(
            [
                f"/audit view {index}",
                f"/audit ok {index}",
                f"/audit no {index} 信息不完整",
                "",
            ]
        )
    lines.extend(
        [
            "若编号无效，请先发送：",
            "/audit list",
        ]
    )
    return "\n".join(lines)


def format_pending_comment_updated_notice(
    *,
    index: int | None,
    group_id: str,
    user_id: str,
    comment: str,
    judgement: str,
) -> str:
    lines = [
        "入群申请内容已更新，需要重新确认",
        "",
    ]
    if index is not None:
        lines.append(f"[{index}] 用户：{user_id}")
    else:
        lines.append(f"用户：{user_id}")
    lines.extend(
        [
            f"群：{group_id}",
            f"验证：{(comment or '')[:80]}",
            f"判断：{judgement}",
            "",
        ]
    )
    if index is not None:
        lines.extend(
            [
                f"/audit view {index}",
                f"/audit ok {index}",
                f"/audit no {index} 信息不完整",
                "",
            ]
        )
    lines.extend(
        [
            "若编号无效，请先发送：",
            "/audit list",
        ]
    )
    return "\n".join(lines)


def format_debug(
    settings: PluginSettings,
    *,
    effective_mode: str,
    mode_source: str,
    student_count: int,
    pending_count: int,
    sync_state: SyncState,
    probe_count: int,
    data_dir: str,
    adapter_probe: dict | None = None,
    admin_session_stats: dict | None = None,
    plugin_version: str | None = None,
    reconcile_logic_version: str | None = None,
    duplicate_policy_version: str | None = None,
    pending_update_policy_version: str | None = None,
    git_commit: str | None = None,
    group_system_msg_probe: dict | None = None,
) -> str:
    return format_status(
        settings,
        effective_mode=effective_mode,
        mode_source=mode_source,
        student_count=student_count,
        pending_count=pending_count,
        sync_state=sync_state,
        probe_count=probe_count,
        data_dir=data_dir,
        adapter_probe=adapter_probe,
        admin_session_stats=admin_session_stats,
        plugin_version=plugin_version,
        reconcile_logic_version=reconcile_logic_version,
        duplicate_policy_version=duplicate_policy_version,
        pending_update_policy_version=pending_update_policy_version,
        git_commit=git_commit,
        group_system_msg_probe=group_system_msg_probe,
    )
