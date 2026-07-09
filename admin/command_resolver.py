from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from admin.labels import DEFAULT_REJECT_REASON
from data_source.students import PendingRequest
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore

ResolveError = Literal[
    "expired_index",
    "not_found",
    "already_processed",
]

ERROR_MESSAGES = {
    "expired_index": "这个编号已经失效，请先发送 /audit list 重新获取最新列表。",
    "not_found": "未找到对应的入群申请，请检查编号或 request id。",
    "already_processed": "这条申请已经处理过了，不能重复操作。",
}


@dataclass
class ResolveResult:
    request: PendingRequest | None = None
    index: int | None = None
    error: ResolveError | None = None

    @property
    def ok(self) -> bool:
        return self.request is not None and self.error is None

    @property
    def message(self) -> str:
        if self.error:
            return ERROR_MESSAGES[self.error]
        return ""


def map_action_error(raw_message: str | None) -> str:
    text = (raw_message or "").lower()
    if not text:
        return "调用 QQ 审批接口失败，申请可能已过期、被撤回，或机器人没有管理员权限。"
    if "expired" in text or "flag" in text or "凭证" in text:
        return "这条申请的审批凭证可能已经过期，请到 QQ 群管理后台确认。"
    if "adapter" in text or "permission" in text or "权限" in text:
        return "调用 QQ 审批接口失败，申请可能已过期、被撤回，或机器人没有管理员权限。"
    return "调用 QQ 审批接口失败，申请可能已过期、被撤回，或机器人没有管理员权限。"


async def resolve_request_ref(
    admin_id: str,
    ref: str,
    *,
    list_cache: AdminListCacheStore,
    requests: RequestsStore,
) -> ResolveResult:
    ref = (ref or "").strip()
    if not ref:
        return ResolveResult(error="not_found")

    index: int | None = None
    request: PendingRequest | None = None

    if ref.isdigit():
        index = int(ref)
        if list_cache.is_expired(admin_id):
            return ResolveResult(index=index, error="expired_index")
        req_id = list_cache.resolve(admin_id, index)
        if not req_id:
            return ResolveResult(index=index, error="expired_index")
        request = await requests.get_by_id(req_id)
        if not request:
            return ResolveResult(index=index, error="not_found")
    else:
        request = await requests.resolve_by_id_or_prefix(ref)
        if not request:
            return ResolveResult(error="not_found")
        index = list_cache.find_index(admin_id, request.id)

    if request.status != "pending" or request.processed_at:
        return ResolveResult(request=request, index=index, error="already_processed")

    return ResolveResult(request=request, index=index)


def normalize_reject_reason(reason: str) -> str:
    reason = (reason or "").strip()
    return reason or DEFAULT_REJECT_REASON


def parse_no_command_reason(message_str: str, ref: str) -> str:
    text = (message_str or "").strip()
    prefix = f"/audit no {ref}".strip()
    if text.startswith(prefix):
        rest = text[len(prefix):].strip()
        return normalize_reject_reason(rest)
    return DEFAULT_REJECT_REASON
