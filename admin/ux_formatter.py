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
            f"最近同步：{_format_local_time(sync_state.last_sync_at)}，"
            f"{sync_state.last_sync_result or '(无)'}",
            f"审批接口：{_adapter_status_text(adapter_probe)}",
            "",
            "下一步：",
            "- 看待处理：/audit list",
            "- 同步学生：/audit sync",
            "- 切自动审核：/audit auto",
        ]
    )
    return "\n".join(lines)


def format_list(items: list, index_map: dict[int, str]) -> str:
    if not items:
        return "目前没有待处理申请。"
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
                list_action_hint(item).replace("编号", str(idx)),
                "",
            ]
        )
    lines.append("编号来自本次列表，30 分钟内有效。无需复制长 request id。")
    return "\n".join(lines)


def _parsed_line(label: str, value) -> str:
    if value is None or value == "":
        return f"{label}：未提供"
    return f"{label}：{value}"


def format_view(item, index: int | None = None) -> str:
    public = item.to_public_dict()
    parsed = public.get("parsed") or {}
    title = f"申请详情 [{index}]" if index is not None else f"申请详情 {public.get('id', '')}"
    lines = [
        title,
        "",
        f"用户：{public.get('user_id', '')}",
        f"群：{public.get('group_id', '')}",
        f"验证：{public.get('comment', '')[:120]}",
        f"时间：{_format_local_time(public.get('created_at'))}",
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
        "可操作：",
    ]
    if index is not None:
        lines.append(f"/audit ok {index}")
        lines.append(f"/audit no {index} 信息不完整")
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
            "- 仅「姓名+学号」或「姓名+通知书编号」强匹配会自动通过",
            "- 弱匹配、信息不足不会自动拒绝",
            "- 建议先完成 /audit sync 并人工抽查几条",
            "",
            "确认切换请发送：",
            "/audit auto confirm",
        ]
    )


def format_off_warning() -> str:
    return "\n".join(
        [
            "切换到 off 后将不再处理新的入群申请。",
            "",
            "确认切换请发送：",
            "/audit off confirm",
        ]
    )


def format_mode_changed(mode: str) -> str:
    return f"运行模式已切换为：{mode}（{mode_label(mode)}）"


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
    )
