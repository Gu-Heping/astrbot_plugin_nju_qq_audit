from __future__ import annotations

from datetime import datetime

from admin.formatter import admin_configured, format_status
from admin.labels import (
    DEFAULT_REJECT_REASON,
    applicant_summary,
    human_judgement,
    list_action_hint,
    mode_label,
    qq_match_label,
    status_label,
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


def format_grad_sync_result(
    *,
    ok: bool,
    sync_state: SyncState | None = None,
    cached_count: int = 0,
    error_name: str | None = None,
) -> str:
    """Human-readable result for /audit sync grad (display only)."""
    state = sync_state or SyncState()
    if ok:
        count = state.filtered_count or cached_count
        lines = [
            "研究生名单同步成功",
            f"缓存人数：{count} 人",
            f"最近同步：{_format_local_time(state.last_sync_at)}",
        ]
        if state.source:
            lines.append(f"来源：{state.source}")
        result = (state.last_sync_result or "").strip()
        if result and result not in {"success", "ok"}:
            lines.append(f"说明：{result}")
        return "\n".join(lines)

    lines = [
        f"研究生名单同步失败：{error_name or '未知错误'}",
        f"已保留旧缓存：{cached_count} 人",
        "",
        "建议：",
        "- 检查 grad_njutable_api_token 与 grad_njutable_table_name 配置",
        "- 运行 /audit debug 查看研究生通道状态",
        "- 稍后再试：/audit sync grad",
    ]
    return "\n".join(lines)


def _adapter_status_text(adapter_probe: dict | None) -> str:
    probe = adapter_probe or {}
    available = probe.get("adapter_action_available", "unknown")
    if available == "yes":
        return "AstrBot adapter 可用"
    if available == "no":
        return "不可用"
    return "未知"


def _channel_status(
    settings: PluginSettings,
    *,
    grad_enabled: bool | None = None,
    grad_target_group_ids: list[str] | frozenset[str] | None = None,
) -> tuple[bool, bool, bool, bool, bool]:
    """Return undergrad/grad flags and whether any audit channel is configured."""
    has_undergrad = bool(settings.target_group_ids)
    grad_on = settings.grad_enabled if grad_enabled is None else grad_enabled
    if grad_target_group_ids is not None:
        has_grad = bool(grad_target_group_ids)
    else:
        has_grad = bool(settings.grad_target_group_ids)
    grad_ready = grad_on and has_grad
    any_channel = has_undergrad or grad_ready
    return has_undergrad, grad_on, has_grad, grad_ready, any_channel


def _home_health(
    settings: PluginSettings,
    adapter_probe: dict | None,
    *,
    grad_enabled: bool | None = None,
    grad_target_group_ids: list[str] | frozenset[str] | None = None,
) -> str:
    _, _, _, _, any_channel = _channel_status(
        settings,
        grad_enabled=grad_enabled,
        grad_target_group_ids=grad_target_group_ids,
    )
    if not any_channel or not admin_configured(settings):
        return "需要配置"
    available = (adapter_probe or {}).get("adapter_action_available", "unknown")
    if available == "no":
        return "有告警"
    return "正常"


def _home_warning_lines(
    settings: PluginSettings,
    *,
    grad_enabled: bool | None = None,
    grad_target_group_ids: list[str] | frozenset[str] | None = None,
) -> list[str]:
    has_undergrad, grad_on, has_grad, _, any_channel = _channel_status(
        settings,
        grad_enabled=grad_enabled,
        grad_target_group_ids=grad_target_group_ids,
    )
    if not any_channel:
        lines = [
            "⚠️ 未配置任何目标群",
        ]
        if grad_on and not has_grad:
            lines.append("研究生已启用但未配置研究生目标群。")
        else:
            lines.append(
                "请到 AstrBot 插件配置里填写 target_group_ids，"
                "或启用并配置 grad_target_group_ids。"
            )
        lines.append("")
        return lines
    if grad_on and not has_grad:
        return [
            "⚠️ 研究生已启用但未配置研究生目标群",
            "请到 AstrBot 插件配置里填写 grad_target_group_ids。",
            "",
        ]
    return []


def format_home(
    settings: PluginSettings,
    *,
    effective_mode: str,
    student_count: int,
    pending_count: int,
    sync_state: SyncState,
    grad_enabled: bool | None = None,
    grad_target_group_ids: list[str] | None = None,
    grad_student_count: int = 0,
    grad_pending_count: int = 0,
    grad_sync_state: SyncState | None = None,
    adapter_probe: dict | None = None,
    releasable_count: int = 0,
    release_running: bool = False,
) -> str:
    lines: list[str] = []
    grad_on = settings.grad_enabled if grad_enabled is None else grad_enabled
    grad_ids = (
        grad_target_group_ids
        if grad_target_group_ids is not None
        else sorted(settings.grad_target_group_ids)
    )
    grad_group_text = ", ".join(sorted(grad_ids)) if grad_ids else "(未配置)"
    health = _home_health(
        settings,
        adapter_probe,
        grad_enabled=grad_on,
        grad_target_group_ids=grad_ids,
    )

    lines.extend(
        _home_warning_lines(
            settings,
            grad_enabled=grad_on,
            grad_target_group_ids=grad_ids,
        )
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
            "",
            "本科：",
            f"- 目标群：{', '.join(sorted(settings.target_group_ids)) or '(未配置)'}",
            f"- 名单人数：{student_count} 人",
            f"- 最近同步：{_format_local_time(sync_state.last_sync_at)}，"
            f"{sync_state.last_sync_result or '(无)'}",
            f"- 待处理数：{pending_count} 条",
            "",
            "研究生：",
            f"- 启用：{'是' if grad_on else '否'}",
            f"- 目标群：{grad_group_text}",
            f"- 名单人数：{grad_student_count} 人",
            f"- 最近同步：{_format_local_time((grad_sync_state or SyncState()).last_sync_at)}，"
            f"{(grad_sync_state or SyncState()).last_sync_result or '(无)'}",
            f"- 待处理数：{grad_pending_count} 条",
            "",
            f"可分批通过：{releasable_count} 条",
            f"分批任务：{'进行中' if release_running else '空闲'}",
            f"审批接口：{_adapter_status_text(adapter_probe)}",
            "",
            "下一步：",
            "- 看待处理：/audit list",
            "- 看研究生：/audit list grad",
            "- 分批通过：/audit release preview",
            "- 同步并补放：/audit catchup preview",
            "- 同步学生：/audit sync",
            "- 同步研究生：/audit sync grad",
            "- 复盘报告：/audit report",
        ]
    )
    return "\n".join(lines)


def format_list(
    items: list,
    index_map: dict[int, str],
    *,
    reconcile_summary=None,
    group_labels: dict[str, str] | None = None,
    user_labels: dict[str, str] | None = None,
    list_profile: str | None = None,
) -> str:
    del index_map  # indexes are positional; map used by caller for cache
    group_labels = group_labels or {}
    user_labels = user_labels or {}
    if not items:
        if list_profile == "graduate":
            body = "\n".join(
                [
                    "目前没有研究生待处理申请。",
                    "可先确认：",
                    "/audit sync grad",
                    "或查看全部：",
                    "/audit list",
                ]
            )
        else:
            body = "目前没有待处理申请。"
    else:
        lines = [f"待处理申请：{len(items)} 条", ""]
        for idx, item in enumerate(items, start=1):
            public = item.to_public_dict()
            summary = applicant_summary(item)
            comment = (public.get("comment") or "")[:80]
            profile = public.get("profile") or "undergraduate"
            profile_label = "研究生" if profile == "graduate" else "本科"
            gid = str(public.get("group_id") or "")
            uid = str(public.get("user_id") or "")
            group_text = group_labels.get(gid) or f"群 {gid}"
            qq_text = user_labels.get(f"{gid}:{uid}") or uid
            hint = list_action_hint(item)
            lines.extend(
                [
                    f"[{idx}] {profile_label}｜{summary}",
                    f"QQ：{qq_text}",
                    f"群：{group_text}",
                    f"验证：{comment or '（空）'}",
                    f"判断：{human_judgement(item)}",
                    "",
                    "操作：",
                ]
            )
            if hint == "ok":
                lines.append(f"/audit ok {idx}")
                lines.append(f"/audit view {idx}")
            else:
                lines.append(f"/audit view {idx}")
                lines.append(f"/audit ok {idx}")
            last_action = public.get("last_action_result") or {}
            if last_action and last_action.get("ok") is False:
                lines.append("提示：上次操作失败，可重试或到 QQ 侧确认。")
            lines.append("若已被其他管理员在 QQ 侧处理：")
            lines.append(f"/audit mark-external {idx} confirm")
            lines.append("若申请已过期、重复或为测试数据：")
            lines.append(f"/audit dismiss {idx} confirm <原因>")
            lines.append("")
        lines.append("编号来自本次列表，30 分钟内有效。")
        body = "\n".join(lines)

    if reconcile_summary is not None:
        extra = reconcile_summary.to_display_lines()
        return body + "\n\n" + "\n".join(extra)
    return body


def _parsed_line(label: str, value) -> str:
    if value is None or value == "":
        return f"{label}：未提供"
    return f"{label}：{value}"


def format_view(
    item,
    index: int | None = None,
    *,
    group_label: str | None = None,
    user_label: str | None = None,
) -> str:
    public = item.to_public_dict()
    parsed = public.get("parsed") or {}
    status = public.get("status", "")
    title = f"申请详情 [{index}]" if index is not None else "申请详情"
    profile = public.get("profile") or "undergraduate"
    profile_label = "研究生" if profile == "graduate" else "本科"
    gid = str(public.get("group_id") or "")
    uid = str(public.get("user_id") or "")
    group_text = (group_label or "").strip() or f"群 {gid}"
    qq_text = (user_label or "").strip() or uid
    summary = applicant_summary(item)
    lines = [
        title,
        "",
        f"类型：{profile_label}",
        f"申请人：{summary}",
        f"QQ：{qq_text}",
        f"群：{group_text}",
        f"验证：{public.get('comment', '')[:120]}",
        f"时间：{_format_local_time(public.get('created_at'))}",
        f"状态：{status_label(status)}",
    ]
    revision = int(public.get("comment_revision") or 0)
    if revision > 0:
        lines.append(f"历史填写：{revision} 次")
    if profile == "graduate":
        match = public.get("match") or {}
        lines.extend(
            [
                "",
                "解析结果：",
                _parsed_line("姓名", parsed.get("name")),
                _parsed_line("录取类型", parsed.get("admission_type")),
                _parsed_line("专业", parsed.get("major_text") or parsed.get("major")),
                _parsed_line("学院", match.get("college") or parsed.get("college")),
                "",
                "判断：",
                f"结果：{human_judgement(item)}",
                f"原因：{public.get('reason') or human_judgement(item)}",
                "",
            ]
        )
    else:
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
        lines.append("已在 QQ 客户端处理，无需再审批。")
        msg = sanitize_action_message((action_result or last_action).get("message"))
        if msg and msg != "（无详情）":
            lines.append(f"说明：{msg}")
    elif status == "dismissed":
        lines.append("已本地关闭，未向 QQ 发起拒绝。")
        lines.append(f"管理员：{public.get('dismissed_by') or public.get('admin_user_id') or '（未知）'}")
        lines.append(f"时间：{_format_local_time(public.get('dismissed_at') or public.get('processed_at'))}")
        lines.append(f"原因：{public.get('dismiss_reason') or '（无）'}")
    elif status == "stale":
        lines.append("QQ 侧已找不到此申请，无法确认是否已入群。")
        msg = sanitize_action_message((action_result or last_action).get("message"))
        if msg and msg != "（无详情）":
            lines.append(f"原因：{msg}")
        lines.append(
            "请到 QQ 群管理后台确认；可 /audit restore 恢复为待处理，"
            "或 /audit mark-external 确认已入群。"
        )
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
    lines.append(qq_match_label((public.get("match") or {}).get("qq_match")))
    lines.append("可操作：")
    if status == "external":
        lines.append("（已在 QQ 侧处理，无需 ok/no）")
    elif status == "dismissed":
        lines.append("（已本地关闭，无需 ok/no；未调用 QQ 拒绝接口）")
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
        lines.append(f"/audit mark-external {index} confirm")
        lines.append(f"/audit dismiss {index} confirm <原因>")
    else:
        lines.append(f"/audit approve {public.get('id')} confirm")
        lines.append(f"/audit reject {public.get('id')} confirm")
        lines.append(f"/audit mark-external {public.get('id')} confirm")
        lines.append(f"/audit dismiss {public.get('id')} confirm <原因>")
    lines.extend(
        [
            "",
            f"记录编号：{public.get('id', '')}",
        ]
    )
    return "\n".join(lines)


def format_ok_result(
    item,
    index: int | None = None,
    *,
    group_label: str | None = None,
    user_label: str | None = None,
) -> str:
    public = item.to_public_dict()
    label = f"[{index}]" if index is not None else public.get("id", "")
    summary = applicant_summary(item)
    gid = str(public.get("group_id") or "")
    uid = str(public.get("user_id") or "")
    group_text = (group_label or "").strip() or f"群 {gid}"
    qq_text = (user_label or "").strip() or uid
    return "\n".join(
        [
            f"已同意申请 {label}",
            "",
            f"申请人：{summary}",
            f"QQ：{qq_text}",
            f"群：{group_text}",
            "处理：管理员手动通过",
        ]
    )


def format_no_result(
    item,
    index: int | None,
    reason: str,
    *,
    group_label: str | None = None,
    user_label: str | None = None,
) -> str:
    public = item.to_public_dict()
    label = f"[{index}]" if index is not None else public.get("id", "")
    summary = applicant_summary(item)
    gid = str(public.get("group_id") or "")
    uid = str(public.get("user_id") or "")
    group_text = (group_label or "").strip() or f"群 {gid}"
    qq_text = (user_label or "").strip() or uid
    return "\n".join(
        [
            f"已拒绝申请 {label}",
            "",
            f"申请人：{summary}",
            f"QQ：{qq_text}",
            f"群：{group_text}",
            f"理由：{reason or DEFAULT_REJECT_REASON}",
            "处理：已向 QQ 发送拒绝",
        ]
    )


def format_auto_result_notice(
    *,
    request_id: str,
    group_id: str,
    user_id: str,
    ok: bool,
    reason: str,
    summary: str | None = None,
    comment: str | None = None,
    match_strength: str | None = None,
    action_message: str | None = None,
    group_label: str | None = None,
    user_label: str | None = None,
) -> str:
    """Human-readable auto-approve success/failure notice for admins."""
    del match_strength  # reserved for future display; judgement uses reason text
    applicant = (summary or "").strip() or str(user_id or "")
    comment_line = (comment or "").strip()[:120]
    group_text = (group_label or "").strip() or f"群 {group_id}"
    qq_text = (user_label or "").strip() or str(user_id or "")
    judgement = (reason or "").strip() or "（无）"

    if ok:
        title = "[入群审核] 已自动通过 ✅"
    else:
        title = "[入群审核] 自动通过失败 ⚠️"

    lines = [
        title,
        "",
        f"申请人：{applicant}",
        f"QQ：{qq_text}",
        f"群：{group_text}",
    ]
    if comment_line:
        lines.append(f"验证：{comment_line}")
    lines.extend(
        [
            "",
            f"判断：{judgement}",
        ]
    )
    if ok:
        lines.append("处理：已同意入群")
        lines.extend(
            [
                "",
                "查看记录：",
                f"/audit view {request_id}",
            ]
        )
    else:
        if action_message:
            lines.append(f"错误：{action_message}")
        lines.extend(
            [
                "",
                "建议：",
                "/audit list",
            ]
        )
    return "\n".join(lines)


def format_auto_warning() -> str:
    return "\n".join(
        [
            "切换到自动审核前请确认：",
            "",
            "- 本科：仅自动通过姓名+学号或姓名+通知书编号的 26 级 strong match",
            "- 研究生（若已启用）：仅自动通过姓名+硕/博+专业（或专业代码）唯一 strong match",
            "- 弱匹配、信息不足不会自动拒绝",
            "- 日常暂停自动放人请用 /audit record，不是 off",
            "- 建议先 /audit sync（及 /audit sync-grad）并人工抽查几条",
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
    profile: str = "undergraduate",
    parsed: dict | None = None,
    summary: str | None = None,
    group_label: str | None = None,
    user_label: str | None = None,
) -> str:
    parsed = parsed or {}
    ref = str(index) if index is not None else None
    group_text = (group_label or "").strip() or f"群 {group_id}"
    qq_text = (user_label or "").strip() or str(user_id or "")
    applicant = (summary or "").strip()
    lines = [
        "新的入群申请需要确认",
        "",
    ]
    if profile == "graduate":
        lines.append("类型：研究生")
        if parsed.get("name"):
            lines.append(f"姓名：{parsed.get('name')}")
        if parsed.get("admission_type"):
            lines.append(f"录取类型：{parsed.get('admission_type')}")
        major = parsed.get("major_text") or parsed.get("major")
        if major:
            lines.append(f"专业：{major}")
        college = parsed.get("college")
        if college:
            lines.append(f"学院：{college}")
        lines.append("")
    elif applicant:
        lines.append(f"申请人：{applicant}")
    lines.extend(
        [
            f"QQ：{qq_text}",
            f"群：{group_text}",
            f"验证：{(comment or '')[:120] or '（空）'}",
            f"判断：{judgement}",
            "",
        ]
    )
    if ref is not None:
        lines.extend(
            [
                f"/audit view {ref}",
                f"/audit ok {ref}",
                f"/audit no {ref}",
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
    summary: str | None = None,
    group_label: str | None = None,
    user_label: str | None = None,
) -> str:
    group_text = (group_label or "").strip() or f"群 {group_id}"
    qq_text = (user_label or "").strip() or str(user_id or "")
    applicant = (summary or "").strip()
    lines = [
        "入群申请内容已更新，需要重新确认",
        "",
    ]
    if index is not None:
        head = f"[{index}] "
    else:
        head = ""
    if applicant:
        lines.append(f"{head}申请人：{applicant}")
    lines.append(f"{head}QQ：{qq_text}" if not applicant else f"QQ：{qq_text}")
    lines.extend(
        [
            f"群：{group_text}",
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
    grad_cache_count: int | None = None,
    grad_sync_state: SyncState | None = None,
    group_overlap_warning: str | None = None,
    config_warnings: list[str] | None = None,
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
        grad_cache_count=grad_cache_count,
        grad_sync_state=grad_sync_state,
        group_overlap_warning=group_overlap_warning,
        config_warnings=config_warnings,
    )
