from config import load_settings
from admin.formatter import format_probe_api, format_status
from data_source.student_cache import SyncState


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_status_without_http_url():
    settings = load_settings(DummyConfig())
    text = format_status(
        settings,
        effective_mode="record-only",
        mode_source="plugin_config",
        student_count=0,
        pending_count=0,
        sync_state=SyncState(),
        probe_count=0,
        data_dir="/tmp",
        adapter_probe={"adapter_action_available": "unknown"},
        admin_session_stats={"cached": 0, "total": 1},
    )
    assert "event_source: astrbot_adapter" in text
    assert "action_backend: astrbot_adapter" in text
    assert "onebot_http_url" not in text
    assert "onebot_access_token" not in text


def test_status_shows_http_url_only_for_http_backend():
    settings = load_settings(
        DummyConfig(
            {
                "onebot_action_backend": "http",
                "onebot_http_url": "http://127.0.0.1:3000/secret",
                "onebot_access_token": "abcd1234",
            }
        )
    )
    text = format_status(
        settings,
        effective_mode="record-only",
        mode_source="plugin_config",
        student_count=0,
        pending_count=0,
        sync_state=SyncState(),
        probe_count=0,
        data_dir="/tmp",
        adapter_probe={"adapter_action_available": "n/a"},
        admin_session_stats={"cached": 0, "total": 0},
    )
    assert "http_url:" in text
    assert "abcd1234" not in text
    assert "action_backend: http" in text


def test_format_probe_api_includes_safe_fields():
    text = format_probe_api(
        {
            "adapter_found": "yes",
            "adapter_action_available": "yes",
            "test_action": "get_login_info",
            "result": "ok",
            "user_id": 123,
            "nickname": "bot",
        }
    )
    assert "probe api" in text
    assert "get_login_info" in text
    assert "set_group_add_request" not in text
