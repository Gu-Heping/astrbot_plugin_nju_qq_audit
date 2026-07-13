from onebot.event_extract import (
    extract_group_decrease,
    extract_group_increase,
    extract_group_request,
)


def test_extract_group_add():
    raw = {
        "post_type": "request",
        "request_type": "group",
        "sub_type": "add",
        "group_id": 1093442531,
        "user_id": 2492835361,
        "comment": "1",
        "flag": "abc123",
    }
    req = extract_group_request(raw)
    assert req is not None
    assert req.group_id == "1093442531"
    assert req.flag == "abc123"


def test_notice_not_request():
    raw = {"post_type": "notice", "notice_type": "group_decrease", "group_id": 1}
    assert extract_group_request(raw) is None


def test_extract_group_increase():
    raw = {
        "post_type": "notice",
        "notice_type": "group_increase",
        "group_id": 796836121,
        "user_id": 2492835361,
        "sub_type": "approve",
    }
    increase = extract_group_increase(raw)
    assert increase is not None
    assert increase.user_id == "2492835361"


def test_extract_group_decrease_leave():
    raw = {
        "post_type": "notice",
        "notice_type": "group_decrease",
        "group_id": 796836121,
        "user_id": 2492835361,
        "sub_type": "leave",
        "operator_id": 2492835361,
    }
    decrease = extract_group_decrease(raw)
    assert decrease is not None
    assert decrease.sub_type == "leave"
    assert decrease.user_id == "2492835361"
