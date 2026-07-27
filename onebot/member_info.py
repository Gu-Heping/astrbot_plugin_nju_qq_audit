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
            if data.get("nickname") or data.get("card") or data.get("shut_up_timestamp") is not None:
                return MemberPresenceCheck(
                    present=None,
                    result_status="ambiguous",
                    returned_user_id=returned_user_text,
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

    if expected_user_id:
        return MemberPresenceCheck(
            present=True,
            result_status="present",
            returned_user_id=returned_user_text,
            returned_group_id=returned_group_text,
            retcode=retcode,
            message=message,
        )

    if returned_user_id is not None:
        return MemberPresenceCheck(
            present=True,
            result_status="present",
            returned_user_id=returned_user_text,
            returned_group_id=returned_group_text,
            retcode=retcode,
            message=message,
        )
    if data.get("nickname") or data.get("card") or data.get("shut_up_timestamp") is not None:
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
    if check.returned_user_id is not None:
        lines.append(f"返回 user_id：{check.returned_user_id}")
    if check.returned_group_id is not None:
        lines.append(f"返回 group_id：{check.returned_group_id}")
    if check.ambiguity_reason:
        lines.append(f"备注：{check.ambiguity_reason}")
    return "\n".join(lines)
