"""探针命令输出格式化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def format_help() -> str:
    return "\n".join(
        [
            "NJU QQ Audit 探针命令（仅私聊）",
            "",
            "/audit_probe - 显示帮助",
            "/audit_probe status - 探针状态",
            "/audit_probe last - 最近一条事件摘要",
            "/audit_probe recent - 最近 10 条事件摘要",
            "/audit_probe raw - 查看最近脱敏 raw（需 log_raw_event=true，仅管理员）",
            "/audit_probe clear confirm - 清空记录（仅管理员）",
        ]
    )


def format_status(
    *,
    probe_enabled: bool,
    recent_count: int,
    last_request_group_at: str | None,
    target_group_ids: str,
    data_dir: Path,
    log_raw_event: bool,
    admin_configured: bool,
) -> str:
    lines = [
        "探针状态",
        f"probe_enabled: {probe_enabled}",
        f"recent_events: {recent_count}",
        f"last_request_group_at: {last_request_group_at or '(无)'}",
        f"target_group_ids: {target_group_ids or '(全部群)'}",
        f"log_raw_event: {log_raw_event}",
        f"data_dir: {data_dir}",
    ]
    if not admin_configured:
        lines.append("admin_qq_ids: (未配置，仅 status/last 对私聊开放)")
    return "\n".join(lines)


def format_event_summary(record: dict[str, Any], index: int | None = None) -> str:
    prefix = f"[{index}] " if index is not None else ""
    lines = [
        f"{prefix}source: {record.get('source', 'astrbot_adapter')}",
        f"post_type: {record.get('post_type') or '(空)'}",
        f"request_type: {record.get('request_type') or '(空)'}",
        f"notice_type: {record.get('notice_type') or '(空)'}",
        f"sub_type: {record.get('sub_type') or '(空)'}",
        f"group_id: {record.get('group_id') or '(空)'}",
        f"user_id: {record.get('user_id') or '(空)'}",
        f"comment: {record.get('comment') or '(空)'}",
        f"flag_present: {record.get('flag_present', 'no')}",
        f"raw_message_present: {record.get('raw_message_present', 'no')}",
        f"received_at: {record.get('received_at') or '(空)'}",
    ]
    return "\n".join(lines)


def format_recent(events: list[dict[str, Any]]) -> str:
    if not events:
        return "暂无最近事件记录。"
    blocks = []
    start = len(events)
    for offset, record in enumerate(events, start=1):
        blocks.append(format_event_summary(record, index=offset))
    return "\n\n".join(blocks)


def format_raw_event(record: dict[str, Any] | None) -> str:
    if record is None:
        return "暂无事件记录。"
    sanitized_raw = record.get("sanitized_raw")
    if sanitized_raw is None:
        return "最近一条事件未保存 sanitized_raw。请开启 log_raw_event=true 后重新触发事件。"
    return json.dumps(sanitized_raw, ensure_ascii=False, indent=2)
