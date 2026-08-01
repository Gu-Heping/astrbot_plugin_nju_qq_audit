"""Read-only QQ history lookup for admin diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field

from admin.labels import undergrad_exclusive_display_lines
from config import redact_tokens_in_string
from data_source.students import PendingRequest

LOOKUP_QQ_DEFAULT_LIMIT = 20
QQ_MIN_LEN = 5
QQ_MAX_LEN = 12


@dataclass
class LookupQqRecord:
    request: PendingRequest
    audit_types: list[str] = field(default_factory=list)


@dataclass
class LookupQqResult:
    qq: str
    total: int
    records: list[LookupQqRecord]
    truncated: bool = False


def validate_lookup_qq(value: str) -> tuple[bool, str]:
    text = (value or "").strip()
    if not text:
        return False, "QQ 号不能为空"
    if not text.isdigit():
        return False, "QQ 号必须为纯数字"
    if len(text) < QQ_MIN_LEN or len(text) > QQ_MAX_LEN:
        return False, "QQ 号长度无效"
    return True, text


def request_qq_values(req: PendingRequest) -> set[str]:
    values: set[str] = set()
    if req.user_id:
        values.add(str(req.user_id).strip())
    parsed = req.parsed or {}
    for key in ("qq", "applicant_qq", "user_qq"):
        raw = parsed.get(key)
        if raw is not None and str(raw).strip():
            values.add(str(raw).strip())
    return values


def request_matches_qq(req: PendingRequest, qq: str) -> bool:
    return qq in request_qq_values(req)


def lookup_display_status(req: PendingRequest) -> str:
    status = req.status or ""
    decision = req.decision or ""
    if status == "pending":
        return "pending"
    if status == "external":
        return "external"
    if status == "stale":
        return "stale"
    if status == "processed":
        if decision == "approve":
            return "approved"
        if decision == "reject":
            return "rejected"
        return "pending"
    if status == "dismissed":
        return "rejected"
    if status == "failed":
        return "pending"
    return status or "pending"


def infer_lookup_source(req: PendingRequest, audit_types: list[str]) -> str:
    for audit_type in audit_types:
        text = (audit_type or "").lower()
        if text == "pending_reparsed" or "reparse" in text:
            return "reparse"
        if "catchup" in text:
            return "catchup"
        if "release" in text or text == "batch_release":
            return "release"
    cmd = (req.admin_command or "").lower()
    if "catchup" in cmd:
        return "catchup"
    if "release" in cmd or cmd == "approve":
        return "release"
    return "event"


def _audit_index(audit_log) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    by_request: dict[str, list[str]] = {}
    by_user: dict[str, list[str]] = {}
    for record in audit_log.read_all():
        if not isinstance(record, dict):
            continue
        audit_type = str(record.get("type") or "")
        req_id = record.get("request_id") or record.get("affected_request_id")
        if req_id:
            by_request.setdefault(str(req_id), []).append(audit_type)
        user_id = record.get("user_id")
        if user_id:
            by_user.setdefault(str(user_id), []).append(audit_type)
    return by_request, by_user


async def lookup_qq_records(
    requests_store,
    audit_log,
    qq: str,
    *,
    limit: int = LOOKUP_QQ_DEFAULT_LIMIT,
) -> LookupQqResult:
    ok, value = validate_lookup_qq(qq)
    if not ok:
        raise ValueError(value)

    all_requests = await requests_store.list_all()
    matched = [req for req in all_requests if request_matches_qq(req, value)]
    matched.sort(key=lambda r: r.created_at, reverse=True)

    by_request, by_user = _audit_index(audit_log)
    user_audit = by_user.get(value, [])

    records: list[LookupQqRecord] = []
    for req in matched[:limit]:
        audit_types = list(by_request.get(req.id, []))
        if not audit_types:
            audit_types = list(user_audit)
        records.append(LookupQqRecord(request=req, audit_types=audit_types))

    return LookupQqResult(
        qq=value,
        total=len(matched),
        records=records,
        truncated=len(matched) > limit,
    )


def sanitize_lookup_output(text: str, settings=None) -> str:
    if settings is not None:
        return redact_tokens_in_string(text, settings)
    return text
