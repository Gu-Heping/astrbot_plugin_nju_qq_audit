from onebot.group_system_msg import (
    SystemJoinRequest,
    match_pending_to_entries,
    parse_group_system_msg_data,
)


def test_parse_group_system_msg_join_requests():
    data = {
        "join_requests": [
            {
                "group_id": 796836121,
                "requester_uin": 2492835361,
                "flag": "abc",
                "message": "hello",
            }
        ]
    }
    parsed = parse_group_system_msg_data(data)
    assert parsed.variant == "napcat_dict"
    assert len(parsed.entries) == 1
    assert parsed.entries[0].flag == "abc"


def test_parse_snowluma_list():
    data = [
        {
            "group_id": 796836121,
            "request_id": 123,
            "requester_uin": 0,
            "message": "测试",
            "flag": "slreq:1:123:796836121:7:0",
        }
    ]
    parsed = parse_group_system_msg_data(data)
    assert parsed.variant == "snowluma_list"
    assert parsed.request_count == 1
    assert parsed.entries[0].requester_uin == "0"


def test_match_pending_prefers_flag():
    entries = [
        SystemJoinRequest("796836121", "2492835361", flag="flag-1", comment="a"),
        SystemJoinRequest("796836121", "2492835361", flag="flag-2", comment="b"),
    ]
    result = match_pending_to_entries(
        flag="flag-1",
        group_id="796836121",
        user_id="2492835361",
        comment="a",
        entries=entries,
    )
    assert result.kind == "unique"
    assert result.entry is not None
    assert result.entry.flag == "flag-1"


def test_match_pending_ambiguous_by_flag():
    entries = [
        SystemJoinRequest("796836121", "1", flag="flag-1"),
        SystemJoinRequest("796836121", "2", flag="flag-1"),
    ]
    result = match_pending_to_entries(
        flag="flag-1",
        group_id="796836121",
        user_id="2492835361",
        comment="",
        entries=entries,
    )
    assert result.kind == "ambiguous"
