from __future__ import annotations

from core.version import PLUGIN_VERSION
from config import PluginSettings, mask_http_url
from data_source.student_cache import SyncState


def format_help(
    *,
    effective_mode: str | None = None,
    pending_count: int | None = None,
    releasable_count: int | None = None,
) -> str:
    lines = [
        f"NJU QQ Audit {PLUGIN_VERSION} 管理命令（私聊）",
        "",
        "推荐流程：",
        "1. /audit record          日常只记录（默认，勿用 off）",
        "2. /audit sync              同步学生数据",
        "3. /audit list              看待处理（短编号 1/2/3，30 分钟有效）",
        "4. /audit catchup preview   同步名单并预览可补放 strong",
        "   /audit catchup confirm   同步 + 重算 + 分批通过",
        "5. /audit ok/no <n>         弱匹配逐条处理",
        "6. /audit report            定期复盘",
    ]

    if effective_mode is not None:
        context_bits = [f"模式 {effective_mode}"]
        if pending_count is not None:
            context_bits.append(f"待处理 {pending_count}")
        if releasable_count is not None:
            context_bits.append(f"可分批 {releasable_count}")
        lines.extend(["", "当前：" + " | ".join(context_bits)])

    lines.extend(
        [
            "",
            "审批：",
            "/audit                      首页状态",
            "/audit list [n]             待处理列表",
            "/audit view <n>             查看详情",
            "/audit lookup <姓名> <学号> [专业]  校对表查询（诊断匹配）",
            "/audit ok <n>               同意（无需 confirm）",
            "/audit no <n> [理由]        拒绝，可附理由",
            "/audit stale [n]            查看 stale 队列（QQ 侧已失效）",
            "/audit restore <n> confirm  将 stale 恢复 pending",
            "/audit mark-external <n> confirm  确认 QQ 侧已处理",
            "/audit dismiss <n> confirm <原因>  本地关闭无效申请（不调 QQ）",
            "",
            "分批放人（仅 strong 26 级 pending，不改变 mode）：",
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
            "",
            "复盘与同步：",
            "/audit unknown [n]          近 7 天未识别汇总 + 样例",
            "/audit report               运营统计（今日/原因分布）",
            "/audit sync                 手动同步 NJUTable / mock",
            "/audit sync status          定时同步状态",
            "",
            "模式：",
            "/audit record               只记录，不自动放人（推荐日常）",
            "/audit manual               每条需人工处理",
            "/audit auto confirm         自动通过 strong 26 级",
            "/audit off confirm          完全停用且不记录（慎用）",
            "",
            "排查：",
            "/audit probe api            测试审批接口",
            "/audit probe last           最近原始入群事件",
            "/audit debug                技术状态",
            "",
            "说明：",
            "- 短编号来自最近一次 /audit list 或入群通知",
            "- 弱匹配、非 26 级、QQ 辅助不会 auto approve",
            "- release：用当前本地名单重算 pending 后分批通过",
            "- catchup：先同步校对表，再重算 pending 并补放新 strong",
            "- lookup：用姓名/学号直接查当前缓存是否能匹配",
            "- 校对表刚更新、历史 pending 未匹配时优先 catchup",
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
