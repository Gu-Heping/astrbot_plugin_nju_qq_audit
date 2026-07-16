from __future__ import annotations

from typing import Any

from admin.labels import status_label


async def resolve_display_labels(
    display: Any | None,
    items: list,
) -> tuple[dict[str, str], dict[str, str]]:
    """Best-effort group/user labels keyed by group_id and \"group:user\".

    Never raises; missing display or API failures fall back to safe text.
    """
    group_labels: dict[str, str] = {}
    user_labels: dict[str, str] = {}
    if not items:
        return group_labels, user_labels

    for item in items:
        gid = str(getattr(item, "group_id", "") or "")
        uid = str(getattr(item, "user_id", "") or "")
        if gid and gid not in group_labels:
            group_labels[gid] = f"群 {gid}"
            if display is not None:
                try:
                    group_labels[gid] = await display.get_group_label(gid)
                except Exception:
                    pass
        ukey = f"{gid}:{uid}"
        if uid and ukey not in user_labels:
            user_labels[ukey] = uid
            if display is not None:
                try:
                    user_labels[ukey] = await display.get_user_label(
                        gid, uid, getattr(item, "parsed", None) or {}
                    )
                except Exception:
                    pass
    return group_labels, user_labels


async def resolve_one_item_labels(
    display: Any | None, item
) -> tuple[str, str]:
    groups, users = await resolve_display_labels(display, [item])
    gid = str(getattr(item, "group_id", "") or "")
    uid = str(getattr(item, "user_id", "") or "")
    return (
        groups.get(gid) or f"群 {gid}",
        users.get(f"{gid}:{uid}") or uid,
    )


def _index_suffix(index: int | None) -> str:
    return f" [{index}]" if index is not None else ""


def format_mark_external_result(item, index: int | None = None) -> str:
    del item
    return "\n".join(
        [
            f"已标记为「QQ侧已处理」{_index_suffix(index)}。",
            "这不会调用 QQ 审批接口，只会从待处理列表移除。",
        ]
    )


def format_dismiss_result(
    item,
    index: int | None,
    reason: str,
    *,
    idempotent: bool = False,
) -> str:
    del item
    suffix = _index_suffix(index)
    if idempotent:
        head = f"申请{suffix} 已是本地关闭状态（未重复修改）。"
    else:
        head = f"已本地关闭申请{suffix}。"
    return "\n".join(
        [
            head,
            "这不会向 QQ 发送拒绝，只用于清理无效/测试/已在 QQ 侧处理的记录。",
            f"原因：{reason}",
        ]
    )


def format_restore_result(item, index: int | None = None) -> str:
    del item
    return "\n".join(
        [
            f"已恢复为「待处理」{_index_suffix(index)}。",
            "注意：QQ 侧可能已经没有这条申请，如再次审批失败，请到 QQ 群管理后台确认。",
        ]
    )


def format_already_terminal_result(item, index: int | None = None) -> str:
    label = f"[{index}]" if index is not None else getattr(item, "id", "")
    status = status_label(getattr(item, "status", "") or "")
    return f"申请 {label} 当前状态为「{status}」，未修改。"
