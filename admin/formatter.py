from __future__ import annotations

from core.version import PLUGIN_VERSION
from config import PluginSettings, mask_http_url
from data_source.student_cache import SyncState
from admin.lookup_qq import (
    LookupQqResult,
    lookup_display_status,
    sanitize_lookup_output,
)
from admin.labels import strength_label


LOOKUP_STATUS_LABELS = {
    "pending": "待处理",
    "approved": "已通过",
    "rejected": "已拒绝",
    "external": "外部处理",
    "stale": "已失效",
}


def _lookup_format_local_time(iso_text: str | None) -> str:
    if not iso_text:
        return "(无)"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_text


def _lookup_status_display(status: str, decision: str = "") -> str:
    key = lookup_display_status(status, decision)
    return LOOKUP_STATUS_LABELS.get(key, key)


def _lookup_group_line(group_id: str, group_labels: dict[str, str] | None) -> str:
    gid = str(group_id or "")
    name = (group_labels or {}).get(gid) or gid
    if not name or name == gid:
        return gid
    if gid and gid in name:
        return name
    return f"{name}({gid})"


def _lookup_application_lines(text: str) -> list[str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines or ["（无）"]


def _lookup_raw_application_summary(application_comment: str, parsed: dict) -> str:
    if application_comment:
        return application_comment
    parsed = parsed or {}
    name = str(parsed.get("name") or "").strip()
    student_id = str(parsed.get("student_id") or "").strip()
    major = str(parsed.get("major_text") or parsed.get("major") or "").strip()
    degree = str(parsed.get("degree") or parsed.get("grad_degree") or "").strip()
    profile = str(parsed.get("_profile") or parsed.get("profile") or "").strip()
    if profile == "graduate" or degree:
        parts = [part for part in (name, major, degree) if part]
    else:
        parts = [part for part in (name, student_id) if part]
        if major and not student_id:
            parts.append(major)
    return " ".join(parts) if parts else "（无）"


def _lookup_parsed_line(parsed: dict) -> str:
    parsed = parsed or {}
    name = str(parsed.get("name") or "").strip()
    student_id = str(parsed.get("student_id") or "").strip()
    major = str(parsed.get("major_text") or parsed.get("major") or "").strip()
    if not any((name, student_id, major)):
        return "未解析"
    return f"{name or '—'}/{student_id or '—'}/{major or '—'}"


def _lookup_simplify_reason(reason: str) -> str:
    import re

    from admin.action_error import classify_action_failure, user_message_for_failure

    text = str(reason or "").strip()
    if not text:
        return "（无）"
    lowered = text.lower()
    if (
        "retcode=" in lowered
        or "retcode:" in lowered
        or '{"status"' in text
        or "'status'" in text
        or "onebot" in lowered
    ):
        retcode_match = re.search(r"retcode[=:\s]+(-?\d+)", text, re.I)
        retcode = int(retcode_match.group(1)) if retcode_match else None
        classified = classify_action_failure(text, retcode)
        if classified.kind != "UNKNOWN":
            msg = user_message_for_failure(classified.kind)
            first = msg.split("。")[0].strip()
            return first or msg[:80]
        return "接口调用异常"
    if len(text) > 100:
        return text[:97] + "..."
    return text


def _lookup_anomaly_line(item, parsed: dict, group_labels: dict[str, str] | None) -> str:
    parsed = parsed or {}
    parts: list[str] = []
    hit_ids = item.exclusive_hit_group_ids or parsed.get("_undergrad_exclusive_group_ids") or []
    if hit_ids or parsed.get("_undergrad_exclusive_hit"):
        labels: list[str] = []
        for gid in hit_ids:
            gid_str = str(gid)
            label = (group_labels or {}).get(gid_str) or gid_str
            labels.append(label)
        parts.append(f"多群互斥({','.join(labels) if labels else '命中'})")
    if item.blacklist_hit or parsed.get("_blacklist_hit"):
        parts.append("黑名单")
    if any("overflow" in str(audit_type or "").lower() for audit_type in (item.audit_types or [])):
        parts.append("overflow")
    if parsed.get("_undergrad_overflow_hit") or parsed.get("_overflow_hit"):
        parts.append("overflow")
    return "；".join(parts)


def _lookup_time_line(created_at: str, last_event_at: str) -> str:
    start = _lookup_format_local_time(created_at)
    if last_event_at and last_event_at != created_at:
        end = _lookup_format_local_time(last_event_at)
        if end != start:
            return f"{start} → {end}"
    return start


def format_help(
    *,
    effective_mode: str | None = None,
    pending_count: int | None = None,
    releasable_count: int | None = None,
    topic: str | None = None,
    settings: PluginSettings | None = None,
) -> str:
    topic_key = (topic or "").strip().lower()
    if topic_key in {"grad", "graduate", "研究生"}:
        return _format_help_grad()
    if topic_key in {"batch", "分批", "release", "catchup"}:
        return _format_help_batch(
            effective_mode=effective_mode,
            pending_count=pending_count,
            releasable_count=releasable_count,
            settings=settings,
        )
    if topic_key in {"debug", "排查", "probe"}:
        return _format_help_debug()
    if topic_key in {"blacklist", "黑名单", "block", "ban"}:
        return _format_help_blacklist()
    if topic_key in {"policy", "routing", "路由", "策略", "本科策略"}:
        return _format_help_policy()
    if topic_key in {"advanced", "adv", "高级", "all", "full"}:
        return _format_help_advanced(
            effective_mode=effective_mode,
            pending_count=pending_count,
            releasable_count=releasable_count,
            settings=settings,
        )
    return _format_help_default(
        effective_mode=effective_mode,
        pending_count=pending_count,
        releasable_count=releasable_count,
    )


def _help_context(
    *,
    effective_mode: str | None,
    pending_count: int | None,
    releasable_count: int | None,
) -> list[str]:
    if effective_mode is None:
        return []
    context_bits = [f"模式 {effective_mode}"]
    if pending_count is not None:
        context_bits.append(f"待处理 {pending_count}")
    if releasable_count is not None:
        context_bits.append(f"可分批 {releasable_count}")
    return ["", "当前：" + " | ".join(context_bits)]


def _format_help_default(
    *,
    effective_mode: str | None,
    pending_count: int | None,
    releasable_count: int | None,
) -> str:
    lines = [
        f"NJU QQ Audit {PLUGIN_VERSION}",
        "",
        "常用：",
        "/audit list",
        "/audit view 1",
        "/audit ok 1",
        "/audit no 1 信息不完整",
        "/audit sync",
        "/audit report",
        "",
        "研究生：",
        "/audit list grad",
        "/audit sync grad",
    ]
    lines.extend(
        _help_context(
            effective_mode=effective_mode,
            pending_count=pending_count,
            releasable_count=releasable_count,
        )
    )
    lines.extend(
        [
            "",
            "更多：",
            "/audit help batch     分批通过/补放",
            "/audit help policy    本科路由/多群策略",
            "/audit help grad      研究生审核说明",
            "/audit help blacklist 黑名单管理",
            "/audit help debug     排查问题",
            "/audit help advanced  高级维护命令",
        ]
    )
    return "\n".join(lines)


def _undergrad_batch_policy_lines() -> list[str]:
    return [
        "本科路由策略（release/catchup）：",
        "- 多群互斥命中：不会在批量流程里自动拒绝，只移出放行队列并转人工确认。",
        "- 满员引导命中：不会在批量流程里自动拒绝，只移出放行队列并转人工确认。",
        "- 自动拒绝只影响实时新申请。",
        "- 查看/切换多群互斥策略：/audit policy",
    ]


def _format_help_policy() -> str:
    return "\n".join(
        [
            f"NJU QQ Audit {PLUGIN_VERSION} · 本科路由策略指令",
            "",
            "查看当前策略：",
            "/audit policy",
            "",
            "切换多群互斥处理方式：",
            "/audit policy exclusive manual confirm",
            "/audit policy exclusive auto-reject confirm",
            "",
            "说明：",
            "- manual：命中「已在其他本科目标群」时转人工，不自动拒绝。",
            "- auto-reject：命中「已在其他本科目标群」时，实时申请自动拒绝。",
            "- release/catchup 不会批量拒绝，只会跳过并转人工确认。",
            "- 黑名单优先级最高。",
            "- 研究生不受本科策略影响。",
            "",
            "满员引导：",
            "- 是否开启、源群、备用群、阈值来自插件配置。",
            "- /audit policy 可查看当前状态；本指令不做 overflow 阈值在线修改。",
        ]
    )


def _format_help_blacklist() -> str:
    from admin.blacklist import format_blacklist_help

    return format_blacklist_help()


def _format_help_grad() -> str:
    return "\n".join(
        [
            f"NJU QQ Audit {PLUGIN_VERSION} · 研究生审核",
            "",
            "常用：",
            "/audit sync grad",
            "/audit list grad",
            "/audit view 1",
            "/audit ok 1",
            "/audit no 1 信息不完整",
            "",
            "研究生批量放行：",
            "/audit release grad preview",
            "/audit release grad 10 confirm",
            "/audit catchup grad preview",
            "/audit catchup grad confirm",
            "/audit report grad",
            "/audit report grad detail 10",
            "",
            "填写格式：",
            "姓名 专业 硕/博",
            "",
            "示例：",
            "张三 马克思主义哲学 硕",
            "李四 010101 博",
            "",
            "规则：",
            "姓名+硕或博唯一命中，或姓名+专业/代码唯一命中，即可进入 release 队列。",
            "缺专业或缺硕/博但仍唯一命中时，会通知管理员并展示名单信息，管理员可提前拒绝。",
            "自动通过仍需姓名 + 专业/代码 + 硕或博 唯一匹配。",
            "其他情况进入人工确认，不会自动拒绝。",
            "",
            "说明：",
            "本科 release/catchup 只处理本科；研究生用 release/catchup grad。",
        ]
    )


def _format_help_batch(
    *,
    effective_mode: str | None,
    pending_count: int | None,
    releasable_count: int | None,
    settings: PluginSettings | None = None,
) -> str:
    lines = [
        f"NJU QQ Audit {PLUGIN_VERSION} · 分批通过 / 补放",
        "",
        "日常只记录：/audit record",
        "",
        "分批通过（用当前本地名单）：",
        "/audit release preview",
        "/audit release 10 confirm",
        "/audit release all confirm",
        "",
        "同步名单并补放：",
        "/audit catchup preview",
        "/audit catchup confirm",
        "/audit catchup 10 confirm",
        "",
        "研究生批量放行：",
        "/audit release grad preview",
        "/audit release grad 10 confirm",
        "/audit catchup grad preview",
        "/audit catchup grad confirm",
        "",
        "筛选条件（须同时满足）：",
        "- 本科申请",
        "- 系统强匹配",
        "- 学号/考生号判断为 26 级",
        "- 仍在待处理队列中",
        "",
        "说明：",
        "- 不改变当前运行模式",
        "- 校对表刚更新时优先 catchup",
        "- 本科 release/catchup 只处理本科；研究生用 release/catchup grad",
        "- 别名：/audit batch strong N confirm、/audit temp N confirm",
        "",
    ]
    lines.extend(_undergrad_batch_policy_lines())
    lines.append("- 指令帮助：/audit help policy")
    lines.extend(
        _help_context(
            effective_mode=effective_mode,
            pending_count=pending_count,
            releasable_count=releasable_count,
        )
    )
    return "\n".join(lines)


def _format_help_debug() -> str:
    return "\n".join(
        [
            f"NJU QQ Audit {PLUGIN_VERSION} · 排查",
            "",
            "/audit probe api            测试审批接口",
            "/audit probe member <群号> <QQ>  检查成员是否在群",
            "/audit probe last           最近原始入群事件",
            "/audit debug                技术状态",
            "/audit lookup <姓名> <学号/通知书/考生号>  校对表查询",
            "/audit lookup qq <QQ号>  查询 QQ 历史申请记录",
            "/audit sync status          定时同步状态",
            "/audit unknown [n]          近 7 天未识别汇总",
            "/audit blacklist list       查看黑名单",
            "/audit blacklist add 3 confirm 家长申请",
            "/audit reparse <n> preview  重解析预览（不写库）",
            "/audit reparse <n> confirm  更新本地解析，不调 QQ",
            "",
            "说明：",
            "- 短编号来自最近一次 /audit list 或入群通知",
            "- 弱匹配、非 26 级、QQ 辅助不会自动通过",
            "- 黑名单优先级高于 strong；命中后不会批量放行",
            "- 黑名单只按 QQ 号拦截，不按学号/考生号拦截",
            "- reparse 只重算本地 pending，不执行 QQ 审批",
        ]
    )


def _format_help_advanced(
    *,
    effective_mode: str | None,
    pending_count: int | None,
    releasable_count: int | None,
    settings: PluginSettings | None = None,
) -> str:
    lines = [
        f"NJU QQ Audit {PLUGIN_VERSION} 管理命令（完整）",
        "",
        "推荐流程：",
        "1. /audit record          日常只记录（默认，勿用 off）",
        "2. /audit sync              同步学生数据",
        "3. /audit list              看待处理（短编号 1/2/3，30 分钟有效）",
        "4. /audit catchup preview   同步名单并预览可补放",
        "   /audit catchup confirm   同步 + 重算 + 分批通过",
        "5. /audit ok/no <n>         弱匹配逐条处理",
        "6. /audit report            定期复盘",
    ]
    lines.extend(
        _help_context(
            effective_mode=effective_mode,
            pending_count=pending_count,
            releasable_count=releasable_count,
        )
    )
    lines.extend(
        [
            "",
            "审批：",
            "/audit                      首页状态",
            "/audit list [n]             待处理列表",
            "/audit view <n>             查看详情",
            "/audit lookup <姓名> <学号/通知书/考生号> [专业]  校对表查询（诊断匹配）",
            "/audit lookup qq <QQ号>  查询 QQ 历史申请记录",
            "/audit ok <n>               同意（无需 confirm）",
            "/audit no <n> [理由]        拒绝，可附理由",
            "/audit stale [n]            查看 QQ 侧已失效的申请",
            "/audit restore <n> confirm  恢复为待处理",
            "/audit mark-external <n> confirm  确认 QQ 侧已处理",
            "/audit dismiss <n> confirm <原因>  本地关闭无效申请（不调 QQ）",
            "/audit sweep preview          预览将本地关闭的非强匹配待处理",
            "/audit sweep confirm <原因>   一键本地关闭非强匹配（保留强匹配）",
            "/audit reparse <n> [rule|ai|auto] preview  重解析预览",
            "/audit reparse <n> [rule|ai|auto] confirm  更新本地解析，不调 QQ",
            "",
            "分批放人（仅本科强匹配 26 级待处理，不改变 mode）：",
            "/audit release              帮助 + 当前可释放数",
            "/audit release preview      按当前缓存重算后预览",
            "/audit release 10 confirm   通过最多 10 条",
            "/audit release all confirm  受单次上限限制",
            "/audit catchup              同步名单并补放（帮助）",
            "/audit catchup preview      拉最新名单 + 重算 + 预览（不放人）",
            "/audit catchup confirm      拉最新名单 + 重算 + 放行（上限内）",
            "/audit catchup 10 confirm   同上，最多 10 条",
            "别名：/audit batch strong N confirm",
            "      /audit temp N confirm",
            "      /audit process strong confirm（兼容）",
        ]
    )
    lines.extend(_undergrad_batch_policy_lines())
    lines.append("- 指令帮助：/audit help policy")
    lines.extend(
        [
            "",
            "研究生分批放人（不改变 mode）：",
            "/audit release grad preview      预览研究生强匹配",
            "/audit release grad 10 confirm   通过最多 10 条研究生",
            "/audit release grad all confirm  受单次上限限制",
            "/audit catchup grad preview      同步研究生名单 + 重算 + 预览",
            "/audit catchup grad confirm      同步 + 重算 + 放行",
            "/audit catchup grad 10 confirm   同上，最多 10 条",
            "别名：/audit grad-release …、/audit grad-catchup …",
            "",
            "复盘与同步：",
            "/audit unknown [n]          近 7 天未识别汇总 + 样例",
            "/audit report               运营统计（今日/原因分布）",
            "/audit report grad          研究生已通过申请复盘摘要",
            "/audit report grad detail 10 研究生复盘脱敏明细",
            "/audit sync                 手动同步 NJUTable / mock",
            "/audit sync grad            同步研究生名单",
            "/audit sync-grad            同上（别名）",
            "/audit sync status          定时同步状态",
            "/audit list grad            仅研究生待处理",
            "/audit list undergraduate   仅本科待处理",
            "",
            "黑名单：",
            "/audit help blacklist",
            "/audit blacklist list",
            "/audit blacklist add 3 confirm 家长申请",
            "/audit blacklist add qq 123456789 confirm 家长号",
            "/audit blacklist remove BL-xxxx confirm",
            "/audit blacklist check <QQ号或编号>",
            "",
            "模式（全局，对本科和研究生都生效）：",
            "/audit record               只记录，不自动放人（推荐日常）",
            "/audit manual               每条需人工处理",
            "/audit auto confirm         自动通过强匹配申请（本科/研究生都会生效）",
            "/audit off confirm          完全停用且不记录（慎用）",
            "",
            "本科路由策略：",
            "/audit policy               查看多群互斥/满员引导状态",
            "/audit policy exclusive manual confirm",
            "/audit policy exclusive auto-reject confirm",
            "/audit help policy          策略指令帮助",
            "",
            "自动通过规则：",
            "- 本科：强匹配且通过 26 级检查",
            "- 研究生 auto：姓名 + 专业/代码 + 硕或博 唯一匹配",
            "- 研究生 release：姓名+硕或博唯一命中，或姓名+专业/代码唯一命中即可；信息缺一项时通知管理员可提前拒绝",
            "- 弱匹配、信息不足、QQ 辅助匹配不会自动通过",
            "- 黑名单优先级高于 strong 匹配；命中后 release/catchup 不会放行",
            "- 黑名单只按 QQ 号拦截，不按学号/考生号拦截，避免误伤学生本人",
            "",
            "完整模式命令：",
            "/audit mode                 查看当前全局模式",
            "/audit mode record-only     同 /audit record",
            "/audit mode manual          同 /audit manual",
            "/audit mode auto confirm    同 /audit auto confirm",
            "/audit mode off confirm     同 /audit off confirm",
            "/audit mode reset confirm   恢复插件配置中的 mode",
            "",
            "注意：",
            "release/catchup 不是 mode：本科命令只处理本科；release/catchup grad 只处理研究生。",
            "",
            "排查：",
            "/audit probe api            测试审批接口",
            "/audit probe last           最近原始入群事件",
            "/audit debug                技术状态",
            "",
            "说明：",
            "- 短编号来自最近一次 /audit list 或入群通知",
            "- 弱匹配、非 26 级、QQ 辅助不会自动通过",
            "- release：用当前本地名单重算待处理后分批通过",
            "- catchup：先同步校对表，再重算待处理并补放新强匹配",
            "- lookup：用姓名/学号/通知书/考生号直接查当前缓存是否能匹配",
            "- sweep：本地批量关闭非强匹配（不调 QQ；适合 QQ 侧已拒但未上报）",
            "- blacklist：按 QQ 号阻止入群申请与批量放行",
            "- 校对表刚更新、历史待处理未匹配时优先 catchup",
            "- 修改目标群请编辑 target_group_ids 后重启",
            "",
            "旧命令（仍可用）：",
            "/audit pending",
            "/audit request <id>",
            "/audit approve <id> confirm",
            "/audit reject <id> confirm",
            "/audit mode ...",
        ]
    )
    return "\n".join(lines)


def format_probe_api(probe: dict) -> str:
    lines = ["probe api"]
    for key in (
        "adapter_found",
        "adapter_action_available",
        "test_action",
        "result",
        "message",
        "user_id",
        "nickname",
        "platform_id",
    ):
        if key in probe and probe[key] not in (None, ""):
            lines.append(f"{key}: {probe[key]}")
    return "\n".join(lines)


def format_status(
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
    grad_enabled: bool | None = None,
    grad_target_group_ids: list[str] | None = None,
    grad_cache_count: int | None = None,
    grad_sync_state: SyncState | None = None,
    group_overlap_warning: str | None = None,
    config_warnings: list[str] | None = None,
) -> str:
    adapter_probe = adapter_probe or {}
    admin_session_stats = admin_session_stats or {"cached": 0, "total": 0}
    adapter_found = str(adapter_probe.get("adapter_found") or "unknown")
    gsm_available = "unknown"
    if group_system_msg_probe:
        if group_system_msg_probe.get("action_status") == "ok":
            gsm_available = "yes"
            if adapter_found in {"no", "unknown", ""}:
                adapter_found = "yes"
        elif group_system_msg_probe.get("action_status") == "failed":
            gsm_available = "no"
        elif group_system_msg_probe.get("group_system_msg_action_available"):
            gsm_available = str(
                group_system_msg_probe.get("group_system_msg_action_available")
            )
    lines = [
        "NJU QQ Audit 状态（debug）",
    ]
    if plugin_version:
        lines.append(f"plugin_version: {plugin_version}")
    if reconcile_logic_version:
        lines.append(f"reconcile_logic_version: {reconcile_logic_version}")
    if duplicate_policy_version:
        lines.append(f"duplicate_policy_version: {duplicate_policy_version}")
    if pending_update_policy_version:
        lines.append(f"pending_update_policy_version: {pending_update_policy_version}")
    if git_commit:
        lines.append(f"git_commit: {git_commit}")
    lines.extend(
        [
        f"effective_mode: {effective_mode}",
        f"mode_source: {mode_source}",
        "event_source: astrbot_adapter",
        f"action_backend: {settings.onebot_action_backend}",
        f"adapter_found: {adapter_found}",
        f"group_system_msg_action_available: {gsm_available}",
        f"student_source: {settings.student_source}",
        f"target_group_ids: {', '.join(sorted(settings.target_group_ids)) or '(未配置)'}",
        "target_group_ids_source: plugin_config",
        f"admin_notify: {settings.admin_notify}",
        f"admin_notify_channels: {admin_session_stats['cached']}/{admin_session_stats['total']}",
        f"students_cache_count: {student_count}",
        f"pending_count: {pending_count}",
        f"last_sync_at: {sync_state.last_sync_at or '(无)'}",
        f"last_sync_result: {sync_state.last_sync_result or '(无)'}",
        f"probe_enabled: {settings.probe_enabled}",
        f"probe_recent_count: {probe_count}",
        f"data_dir: {data_dir}",
        ]
    )
    ge = settings.grad_enabled if grad_enabled is None else grad_enabled
    lines.append(f"grad_enabled: {ge}")
    gids = (
        grad_target_group_ids
        if grad_target_group_ids is not None
        else sorted(settings.grad_target_group_ids)
    )
    lines.append(f"grad_target_group_ids: {', '.join(gids) or '(未配置)'}")
    if grad_cache_count is not None:
        lines.append(f"grad_cache_count: {grad_cache_count}")
    if grad_sync_state is not None:
        lines.append(f"grad_last_sync_at: {grad_sync_state.last_sync_at or '(无)'}")
        lines.append(
            f"grad_last_sync_result: {grad_sync_state.last_sync_result or '(无)'}"
        )
    if group_overlap_warning:
        lines.append(f"group_overlap_warning: {group_overlap_warning}")
    for w in config_warnings or []:
        lines.append(f"config_warning: {w}")
    if group_system_msg_probe:
        lines.append("group_system_msg_probe:")
        for key in (
            "action_status",
            "retcode",
            "data_type",
            "request_count",
            "top_level_shape",
            "first_request_fields",
            "parser_variant",
            "group_system_msg_action_available",
            "snapshot_saturated",
            "snapshot_complete",
        ):
            if key in group_system_msg_probe:
                lines.append(f"  {key}: {group_system_msg_probe[key]}")
    if settings.onebot_action_backend == "http":
        lines.append(f"http_url: {mask_http_url(settings.onebot_http_url)}")
    if not settings.target_group_ids:
        lines.append("警告: target_group_ids 为空，不会处理任何入群申请。")
    if not admin_configured(settings):
        lines.append("admin_qq_ids: (未配置，仅 help/status/probe 调试开放)")
    elif settings.admin_notify and admin_session_stats["cached"] < admin_session_stats["total"]:
        lines.append("提示: 部分管理员尚未私聊 /audit status，主动通知可能无法送达。")
    return "\n".join(lines)


def admin_configured(settings: PluginSettings) -> bool:
    return bool(settings.admin_qq_ids)


def format_pending_list(items: list) -> str:
    if not items:
        return "暂无 pending 请求。"
    lines = []
    for item in items:
        public = item.to_public_dict()
        lines.append(
            "\n".join(
                [
                    f"id: {public['id']}",
                    f"group_id: {public['group_id']}",
                    f"user_id: {public['user_id']}",
                    f"comment: {public['comment'][:80]}",
                    f"decision: {public['decision']}",
                    f"match_strength: {public['match_strength']}",
                    f"created_at: {public['created_at']}",
                ]
            )
        )
    return "\n\n".join(lines)


def format_request_detail(item) -> str:
    public = item.to_public_dict()
    lines = [
        f"id: {public['id']}",
        f"group_id: {public['group_id']}",
        f"user_id: {public['user_id']}",
        f"comment: {public['comment']}",
        f"parsed: {public['parsed']}",
        f"match: {public['match']}",
        f"decision: {public['decision']}",
        f"confidence: {public['confidence']}",
        f"reason: {public['reason']}",
        f"created_at: {public['created_at']}",
        f"status: {public['status']}",
    ]
    return "\n".join(lines)


def format_lookup_qq_result(
    result: LookupQqResult,
    *,
    group_labels: dict[str, str] | None = None,
    settings: PluginSettings | None = None,
) -> str:
    if result.total == 0:
        text = "\n".join(
            [
                "QQ查询",
                "",
                f"QQ：{result.qq}",
                "历史申请：未找到",
            ]
        )
        return sanitize_lookup_output(text, settings)

    lines = [
        "QQ查询",
        "",
        f"QQ：{result.qq}",
        f"历史申请：{result.total}条",
    ]
    if result.truncated:
        lines.append(f"（仅展示最近 {len(result.records)} 条，共 {result.total} 条）")

    for index, item in enumerate(result.records, start=1):
        parsed = item.parsed or {}
        profile = item.profile or parsed.get("_profile") or "undergraduate"
        profile_label = "研究生" if profile == "graduate" else "本科"
        group_text = _lookup_group_line(str(item.group_id or ""), group_labels)
        status_text = _lookup_status_display(item.status, item.decision)
        strength_text = strength_label(item.match_strength or "none")
        raw_application = _lookup_raw_application_summary(item.application_comment, parsed)
        application_lines = _lookup_application_lines(raw_application)
        parsed_line = _lookup_parsed_line(parsed)
        reason_text = _lookup_simplify_reason(item.reason or "")
        anomaly_text = _lookup_anomaly_line(item, parsed, group_labels)

        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{index}] {profile_label}｜{group_text}")
        if len(application_lines) == 1:
            lines.append(f"原始申请：{application_lines[0]}")
        else:
            lines.append("原始申请：")
            lines.extend(application_lines)
        lines.append(f"解析：{parsed_line}")
        lines.append(f"结果：{status_text}｜匹配:{strength_text}")
        lines.append(f"时间：{_lookup_time_line(item.created_at, item.last_event_at)}")
        lines.append(f"原因：{reason_text}")
        if anomaly_text:
            lines.append(f"异常：{anomaly_text}")

    return sanitize_lookup_output("\n".join(lines), settings)


def format_stats(stats: dict[str, int]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in stats.items())


def format_probe_status(
    settings: PluginSettings, probe_count: int, data_dir: str, last_request_group_at: str | None
) -> str:
    return "\n".join(
        [
            "探针状态",
            f"probe_enabled: {settings.probe_enabled}",
            f"log_raw_event: {settings.log_raw_event}",
            f"recent_events: {probe_count}",
            f"last_request_group_at: {last_request_group_at or '(无)'}",
            f"data_dir: {data_dir}",
        ]
    )
