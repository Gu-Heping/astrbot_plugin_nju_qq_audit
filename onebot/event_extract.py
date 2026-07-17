from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from probe.sanitizer import get_field, to_plain_dict


@dataclass
class GroupJoinRequest:
    group_id: str
    user_id: str
    comment: str
    flag: str
    sub_type: str
    self_id: str | None = None
    raw_event: dict[str, Any] | None = None


def extract_raw_dict(message_obj: Any) -> dict[str, Any] | None:
    raw = getattr(message_obj, "raw_message", None)
    if raw is None:
        return None
    plain = to_plain_dict(raw)
    return plain


def extract_group_request(raw: dict[str, Any] | None) -> GroupJoinRequest | None:
    if not raw:
        return None
    if get_field(raw, "post_type") != "request":
        return None
    if get_field(raw, "request_type") != "group":
        return None
    sub_type = get_field(raw, "sub_type")
    if sub_type not in {"add", "invite"}:
        return None
    flag = get_field(raw, "flag")
    if not flag:
        return None
    group_id = get_field(raw, "group_id")
    user_id = get_field(raw, "user_id")
    if group_id is None or user_id is None:
        return None
    return GroupJoinRequest(
        group_id=str(group_id),
        user_id=str(user_id),
        comment=str(get_field(raw, "comment", "") or ""),
        flag=str(flag),
        sub_type=str(sub_type),
        self_id=str(get_field(raw, "self_id")) if get_field(raw, "self_id") is not None else None,
        raw_event=raw,
    )


@dataclass
class GroupMemberIncrease:
    group_id: str
    user_id: str
    sub_type: str | None = None
    operator_id: str | None = None
    self_id: str | None = None
    raw_event: dict[str, Any] | None = None


@dataclass
class GroupMemberDecrease:
    group_id: str
    user_id: str
    sub_type: str | None = None
    operator_id: str | None = None
    self_id: str | None = None
    raw_event: dict[str, Any] | None = None


def extract_group_decrease(raw: dict[str, Any] | None) -> GroupMemberDecrease | None:
    if not raw:
        return None
    if get_field(raw, "post_type") != "notice":
        return None
    if get_field(raw, "notice_type") != "group_decrease":
        return None
    group_id = get_field(raw, "group_id")
    user_id = get_field(raw, "user_id")
    if group_id is None or user_id is None:
        return None
    sub_type = get_field(raw, "sub_type")
    operator_id = get_field(raw, "operator_id")
    return GroupMemberDecrease(
        group_id=str(group_id),
        user_id=str(user_id),
        sub_type=str(sub_type) if sub_type is not None else None,
        operator_id=str(operator_id) if operator_id is not None else None,
        self_id=str(get_field(raw, "self_id")) if get_field(raw, "self_id") is not None else None,
        raw_event=raw,
    )


def extract_group_increase(raw: dict[str, Any] | None) -> GroupMemberIncrease | None:
    if not raw:
        return None
    if get_field(raw, "post_type") != "notice":
        return None
    if get_field(raw, "notice_type") != "group_increase":
        return None
    group_id = get_field(raw, "group_id")
    user_id = get_field(raw, "user_id")
    if group_id is None or user_id is None:
        return None
    sub_type = get_field(raw, "sub_type")
    operator_id = get_field(raw, "operator_id")
    return GroupMemberIncrease(
        group_id=str(group_id),
        user_id=str(user_id),
        sub_type=str(sub_type) if sub_type is not None else None,
        operator_id=str(operator_id) if operator_id is not None else None,
        self_id=str(get_field(raw, "self_id")) if get_field(raw, "self_id") is not None else None,
        raw_event=raw,
    )


def is_notice_event(raw: dict[str, Any] | None) -> bool:
    if not raw:
        return False
    return get_field(raw, "post_type") == "notice"
