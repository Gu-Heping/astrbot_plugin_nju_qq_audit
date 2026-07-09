from onebot.event_extract import extract_group_request


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
