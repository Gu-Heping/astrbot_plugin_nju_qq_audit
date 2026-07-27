from __future__ import annotations

from dataclasses import dataclass

from data_source.students import ActionResult


_NOT_FOUND_MARKERS = (
    "not found",
    "找不到",
    "无法获取",
    "不在",
    "不存在",
    "not in",
)

_PROBE_DISPLAY_FIELDS = (
    "user_id",
    "group_id",
    "nickname",
    "role",
    "join_time",
    "last_sent_time",
    "level",
    "card",
    "title",
    "title_expire_time",
    "card_changeable",
    "unfriendly",
    "shut_up_timestamp",
)

_STRONG_MEMBER_ROLES = frozenset({"owner", "admin"})
_STRONG_MEMBER_TIME_FIELDS = ("join_time", "last_sent_time")
_STRONG_MEMBER_TEXT_FIELDS = ("card", "title")

_PROBE_HIDDEN_FIELDS = frozenset(
    {
        "raw_event",
        "flag",
        "token",
    }
)


@dataclass(frozen=True)
class MemberPresenceCheck:
    present: bool | None
    result_status: str
    returned_user_id: str | None = None
    returned_group_id: str | None = None
    retcode: int | None = None
    message: str | None = None
    ambiguity_reason: str | None = None


def _short_message(message: str | None) -> str | None:
    if not message:
        return None
    text = str(message).strip()
    if len(text) > 120:
        return text[:117] + "..."
    return text


def _positive_time_value(value) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _has_group_member_evidence(data: dict) -> bool:
    role = str(data.get("role") or "").strip().lower()
    if role in _STRONG_MEMBER_ROLES:
        return True

    for key in _STRONG_MEMBER_TIME_FIELDS:
        if _positive_time_value(data.get(key)) is not None:
            return True

    for key in _STRONG_MEMBER_TEXT_FIELDS:
        if str(data.get(key) or "").strip():
            return True

    return False


def _collect_strong_member_evidence_parts(data: dict) -> list[str]:
    parts: list[str] = []
    role = str(data.get("role") or "").strip().lower()
    if role in _STRONG_MEMBER_ROLES:
        parts.append(f"role={data.get('role')}")

    for key in _STRONG_MEMBER_TIME_FIELDS:
        value = _positive_time_value(data.get(key))
        if value is not None:
            parts.append(f"{key}={value}")

    for key in _STRONG_MEMBER_TEXT_FIELDS:
        text = str(data.get(key) or "").strip()
        if text:
            parts.append(f"{key}={text}")

    return parts


def _format_returned_fields(data: dict | None) -> str:
    if not isinstance(data, dict) or not data:
        return "（无）"
    parts: list[str] = []
    for key in _PROBE_DISPLAY_FIELDS:
        if key in _PROBE_HIDDEN_FIELDS or str(key).startswith("_"):
            continue
        value = data.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return "、".join(parts) if parts else "（无）"


def _format_member_evidence(data: dict | None) -> str:
    if not isinstance(data, dict) or not data:
        return "无"
    parts = _collect_strong_member_evidence_parts(data)
    return " / ".join(parts) if parts else "无"


def inspect_user_in_group(
    result: ActionResult,
    *,
    expected_group_id: str | None = None,
    expected_user_id: str | None = None,
) -> MemberPresenceCheck:
    """Classify get_group_member_info result. True=in group, False=not, None=ambiguous."""
    retcode = getattr(result, "retcode", None)
    message = _short_message(result.message)

    if not result.ok:
        text = (result.message or "").lower()
        if any(marker in text for marker in _NOT_FOUND_MARKERS):
            return MemberPresenceCheck(
                present=False,
                result_status="not_found",
                retcode=retcode,
                message=message,
            )
        return MemberPresenceCheck(
            present=None,
            result_status="ambiguous",
            retcode=retcode,
            message=message,
        )

    data = result.data
    if not isinstance(data, dict):
        return MemberPresenceCheck(
            present=None,
            result_status="ambiguous",
            retcode=retcode,
            message=message,
            ambiguity_reason="invalid_data",
        )

    returned_user_id = data.get("user_id")
    returned_group_id = data.get("group_id")
    returned_user_text = (
        str(returned_user_id) if returned_user_id is not None else None
    )
    returned_group_text = (
        str(returned_group_id) if returned_group_id is not None else None
    )

    if expected_user_id:
        if returned_user_id is None:
            if _has_group_member_evidence(data):
                return MemberPresenceCheck(
                    present=None,
                    result_status="ambiguous",
                    returned_group_id=returned_group_text,
                    retcode=retcode,
                    message=message,
                    ambiguity_reason="missing_user_id",
                )
            return MemberPresenceCheck(
                present=False,
                result_status="not_found",
                returned_group_id=returned_group_text,
                retcode=retcode,
                message=message,
            )
        if str(returned_user_id) != str(expected_user_id):
            return MemberPresenceCheck(
                present=None,
                result_status="ambiguous",
                returned_user_id=returned_user_text,
                returned_group_id=returned_group_text,
                retcode=retcode,
                message=message,
                ambiguity_reason="identity_mismatch",
            )

    if expected_group_id and returned_group_id is not None:
        if str(returned_group_id) != str(expected_group_id):
            return MemberPresenceCheck(
                present=None,
                result_status="ambiguous",
                returned_user_id=returned_user_text,
                returned_group_id=returned_group_text,
                retcode=retcode,
                message=message,
                ambiguity_reason="group_mismatch",
            )

    has_user_id = returned_user_id is not None or bool(expected_user_id)
    if has_user_id:
        if not _has_group_member_evidence(data):
            return MemberPresenceCheck(
                present=None,
                result_status="ambiguous",
                returned_user_id=returned_user_text,
                returned_group_id=returned_group_text,
                retcode=retcode,
                message=message,
                ambiguity_reason="missing_member_evidence",
            )
        return MemberPresenceCheck(
            present=True,
            result_status="present",
            returned_user_id=returned_user_text,
            returned_group_id=returned_group_text,
            retcode=retcode,
            message=message,
        )

    if _has_group_member_evidence(data):
        return MemberPresenceCheck(
            present=None,
            result_status="ambiguous",
            returned_group_id=returned_group_text,
            retcode=retcode,
            message=message,
            ambiguity_reason="missing_user_id",
        )
    return MemberPresenceCheck(
        present=False,
        result_status="not_found",
        returned_group_id=returned_group_text,
        retcode=retcode,
        message=message,
    )


def is_user_in_group(
    result: ActionResult,
    *,
    expected_group_id: str | None = None,
    expected_user_id: str | None = None,
) -> bool | None:
    """True=在群, False=不在群, None=无法确认。"""
    return inspect_user_in_group(
        result,
        expected_group_id=expected_group_id,
        expected_user_id=expected_user_id,
    ).present


def format_member_presence_label(present: bool | None) -> str:
    if present is True:
        return "在群"
    if present is False:
        return "不在群"
    return "无法确认"


def format_member_probe_report(
    *,
    group_id: str,
    user_id: str,
    check: MemberPresenceCheck,
    data: dict | None = None,
) -> str:
    lines = [
        "get_group_member_info 检查结果",
        "",
        f"群：{group_id}",
        f"QQ：{user_id}",
        f"结果：{format_member_presence_label(check.present)}",
    ]
    if check.retcode is not None:
        lines.append(f"retcode：{check.retcode}")
    if check.message:
        lines.append(f"message：{check.message}")
    lines.append(f"返回字段：{_format_returned_fields(data)}")
    lines.append(f"成员证据：{_format_member_evidence(data)}")
    if check.ambiguity_reason:
        lines.append(f"备注：{check.ambiguity_reason}")
    return "\n".join(lines)
