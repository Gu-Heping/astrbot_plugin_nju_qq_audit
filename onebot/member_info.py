from __future__ import annotations

from data_source.students import ActionResult


def is_user_in_group(result: ActionResult) -> bool | None:
    """True=在群, False=不在群, None=无法确认。"""
    if not result.ok:
        return None
    data = result.data
    if not isinstance(data, dict):
        return None
    if data.get("user_id") is not None or data.get("nickname"):
        return True
    if data.get("shut_up_timestamp") is not None:
        return True
    return False
