from __future__ import annotations

import asyncio

from config import PluginSettings
from data_source.students import ActionResult, PendingRequest
from onebot.actions import ActionClient
from onebot.reject_reason import (
    log_reject_delay,
    log_reject_dispatch,
    log_reject_final_payload,
    log_reject_reason_generated,
    resolve_qq_reject_reason,
)


async def execute_qq_reject(
    actions: ActionClient,
    settings: PluginSettings,
    req: PendingRequest,
    *,
    reason: str,
    source: str,
    decision: str | None = None,
) -> ActionResult:
    """唯一 QQ 拒绝执行入口：reason fallback、日志、set_group_add_request。"""
    effective_reason = resolve_qq_reject_reason(reason)
    log_reject_reason_generated(effective_reason, source=source)
    delay = 0.0
    if source != "manual":
        delay = float(settings.auto_reject_delay_sec or 0)
        if delay > 0:
            log_reject_delay(
                source=source,
                flag=req.flag,
                group_id=req.group_id,
                delay=delay,
            )
            await asyncio.sleep(delay)
    log_reject_dispatch(
        source=source,
        group_id=req.group_id,
        user_id=req.user_id,
        flag=req.flag,
        reason=effective_reason,
        delay=delay,
        sub_type=req.sub_type,
        approve=False,
        decision=decision,
    )
    log_reject_final_payload(
        source=source,
        flag=req.flag,
        sub_type=req.sub_type,
        approve=False,
        reason=effective_reason,
        request_id=req.id,
    )
    return await actions.set_group_add_request(
        req.flag,
        req.sub_type,
        False,
        effective_reason,
        request_id=req.id,
        reject_source=source,
        request_time=req.created_at,
    )
