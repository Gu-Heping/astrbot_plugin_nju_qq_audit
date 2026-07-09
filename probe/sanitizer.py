"""OneBot 原始事件脱敏与分类工具。"""

from __future__ import annotations

from typing import Any

SENSITIVE_KEYS = frozenset(
    {
        "flag",
        "token",
        "access_token",
        "authorization",
        "Authorization",
        "onebot_access_token",
    }
)

def truncate_comment(text: Any, max_len: int = 80) -> str:
    if text is None:
        return ""
    value = str(text).strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def to_plain_dict(obj: Any) -> dict[str, Any] | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return dict(obj)
    try:
        return dict(obj)
    except (TypeError, ValueError):
        pass
    if hasattr(obj, "items"):
        try:
            return {str(k): v for k, v in obj.items()}
        except (TypeError, ValueError):
            pass
    if hasattr(obj, "__dict__"):
        data = vars(obj)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if not str(k).startswith("_")}
    return None


def get_field(raw: Any, key: str, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, dict):
        return raw.get(key, default)
    try:
        return raw[key]
    except (KeyError, TypeError, IndexError):
        pass
    return getattr(raw, key, default)


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            str(k): "***" if str(k) in SENSITIVE_KEYS else sanitize(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize(item) for item in obj]
    if isinstance(obj, tuple):
        return [sanitize(item) for item in obj]
    plain = to_plain_dict(obj)
    if plain is not None and plain is not obj:
        return sanitize(plain)
    return obj


def flag_present(raw: Any) -> bool:
    value = get_field(raw, "flag")
    if value is None:
        return False
    return bool(str(value).strip())


def classify_raw_message(raw: Any) -> dict[str, Any] | None:
    """识别疑似群 request/notice 事件，无法识别时返回 None。"""
    if raw is None:
        return None

    post_type = get_field(raw, "post_type")
    if not post_type:
        return None

    post_type = str(post_type)
    request_type = get_field(raw, "request_type")
    notice_type = get_field(raw, "notice_type")
    sub_type = get_field(raw, "sub_type")

    request_type_str = str(request_type) if request_type is not None else None
    notice_type_str = str(notice_type) if notice_type is not None else None
    sub_type_str = str(sub_type) if sub_type is not None else None

    is_suspected = False
    if post_type == "request" and request_type_str == "group" and sub_type_str in {
        "add",
        "invite",
    }:
        is_suspected = True
    elif post_type == "notice" and notice_type_str in {
        "group_decrease",
        "group_increase",
    }:
        is_suspected = True

    if not is_suspected:
        return None

    group_id = get_field(raw, "group_id")
    user_id = get_field(raw, "user_id")
    comment = get_field(raw, "comment", "")

    return {
        "post_type": post_type,
        "request_type": request_type_str,
        "notice_type": notice_type_str,
        "sub_type": sub_type_str,
        "group_id": str(group_id) if group_id is not None else "",
        "user_id": str(user_id) if user_id is not None else "",
        "comment": truncate_comment(comment),
        "flag_present": "yes" if flag_present(raw) else "no",
        "raw_message_present": "yes",
        "raw_message_missing": False,
    }


def build_missing_raw_summary(
    *,
    group_id: str = "",
    user_id: str = "",
    message_obj_type: str = "",
) -> dict[str, Any]:
    return {
        "post_type": "",
        "request_type": None,
        "notice_type": None,
        "sub_type": None,
        "group_id": group_id,
        "user_id": user_id,
        "comment": f"raw_message missing (message_obj={message_obj_type})",
        "flag_present": "no",
        "raw_message_present": "no",
        "raw_message_missing": True,
    }


def parse_id_list(value: str) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}
