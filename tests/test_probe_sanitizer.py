from probe.sanitizer import sanitize, classify_raw_message, flag_present


def test_sanitize_flag():
    raw = {"flag": "secret", "token": "abc", "comment": "hi"}
    result = sanitize(raw)
    assert result["flag"] == "***"
    assert result["token"] == "***"


def test_classify_request_group_add():
    summary = classify_raw_message(
        {
            "post_type": "request",
            "request_type": "group",
            "sub_type": "add",
            "group_id": 1,
            "user_id": 2,
            "flag": "x",
        }
    )
    assert summary is not None
    assert summary["flag_present"] == "yes"


def test_flag_present_without_leak():
    assert flag_present({"flag": "abc"})
    sanitized = sanitize({"flag": "abc"})
    assert sanitized["flag"] == "***"
