from admin.formatter import format_probe_api


def test_probe_api_formatter_does_not_mention_dangerous_action():
    text = format_probe_api(
        {
            "adapter_found": "yes",
            "adapter_action_available": "yes",
            "test_action": "get_group_list",
            "result": "ok",
            "message": "ok",
        }
    )
    assert "set_group_add_request" not in text
    assert "get_group_list" in text
