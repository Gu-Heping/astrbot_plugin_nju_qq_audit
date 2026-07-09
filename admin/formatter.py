from __future__ import annotations

from config import PluginSettings, mask_http_url
from data_source.student_cache import SyncState


def format_help() -> str:
    return "\n".join(
        [
            "NJU QQ Audit 管理命令（仅私聊）",
            "",
            "/audit help - 显示帮助",
            "/audit status - 运行状态",
            "/audit mode - 查看/切换运行模式",
            "/audit sync - 同步学生数据",
            "/audit pending [n] - 待审核列表",
            "/audit request <id> - 查看请求详情",
            "/audit approve <id> confirm - 人工同意",
            "/audit reject <id> confirm - 人工拒绝",
            "/audit process strong confirm - 批量处理 strong match",
            "/audit stats - 统计信息",
            "/audit probe status|last|recent|api - 探针命令",
        ]
    )


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
) -> str:
    adapter_probe = adapter_probe or {}
    admin_session_stats = admin_session_stats or {"cached": 0, "total": 0}
    adapter_available = adapter_probe.get("adapter_action_available", "unknown")
    lines = [
        "NJU QQ Audit 状态",
        f"effective_mode: {effective_mode}",
        f"mode_source: {mode_source}",
        "event_source: astrbot_adapter",
        f"action_backend: {settings.onebot_action_backend}",
        f"adapter_action_available: {adapter_available}",
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
