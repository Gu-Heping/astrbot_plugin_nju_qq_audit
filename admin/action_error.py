from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActionFailureKind = Literal["TRANSIENT", "PERMISSION", "ADAPTER", "STALE", "UNKNOWN"]

_STALE_MARKERS = (
    "expired",
    "flag",
    "not found",
    "already handled",
    "invalid flag",
    "no such request",
    "请求不存在",
    "请求已处理",
    "已过期",
    "已被处理",
    "凭证",
    "找不到",
)

_PERMISSION_MARKERS = (
    "permission",
    "权限",
    "not admin",
    "no permission",
    "无权",
)

_ADAPTER_MARKERS = (
    "adapter",
    "not available",
    "不可用",
)

_TRANSIENT_MARKERS = (
    "timeout",
    "connection",
    "network",
    "timed out",
    "连接",
    "超时",
)


@dataclass(frozen=True)
class ClassifiedFailure:
    kind: ActionFailureKind
    message: str


def classify_action_failure(message: str | None, retcode: int | None = None) -> ClassifiedFailure:
    text = (message or "").lower()
    if any(m in text for m in _STALE_MARKERS):
        return ClassifiedFailure("STALE", message or "")
    if any(m in text for m in _PERMISSION_MARKERS):
        return ClassifiedFailure("PERMISSION", message or "")
    if any(m in text for m in _ADAPTER_MARKERS):
        return ClassifiedFailure("ADAPTER", message or "")
    if any(m in text for m in _TRANSIENT_MARKERS):
        return ClassifiedFailure("TRANSIENT", message or "")
    if retcode in {404, 1404}:
        return ClassifiedFailure("STALE", message or "")
    return ClassifiedFailure("UNKNOWN", message or "")


def user_message_for_failure(kind: ActionFailureKind, *, became_external: bool = False) -> str:
    if became_external:
        return "该用户已在群内，队列已标记为 external。"
    if kind == "STALE":
        return (
            "QQ 侧已找不到这条申请，可能已被处理、撤回或过期。"
            "已从待处理列表移到 stale。请到 QQ 群管理后台确认。"
        )
    if kind == "PERMISSION":
        return "审批失败：机器人可能没有群管理员权限。申请仍保留在 pending，可修复权限后重试。"
    if kind == "ADAPTER":
        return (
            "审批失败：AstrBot adapter action 不可用。"
            "申请仍保留在 pending，可运行 /audit probe api 或启用 HTTP fallback。"
        )
    if kind == "TRANSIENT":
        return "审批接口临时失败，申请仍保留在 pending，可稍后重试。"
    return (
        "调用 QQ 审批接口失败，申请可能已过期、被撤回，或机器人没有管理员权限。"
        "请稍后重试或到 QQ 群管理后台确认。"
    )


def format_action_outcome_message(
    result_message: str | None,
    retcode: int | None,
    *,
    final_status: str,
) -> str:
    classified = classify_action_failure(result_message, retcode)
    became_external = final_status == "external"
    return user_message_for_failure(classified.kind, became_external=became_external)
