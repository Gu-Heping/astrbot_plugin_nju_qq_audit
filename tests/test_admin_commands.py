from admin.permissions import can_run_command, admin_configured
from config import load_settings


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class DummyEvent:
    FRIEND = "FRIEND_MESSAGE"
    GROUP = "GROUP_MESSAGE"

    def __init__(self, sender_id: str, private: bool = True):
        self._sender_id = sender_id
        self._private = private

    def get_sender_id(self):
        return self._sender_id

    def get_message_type(self):
        return self.FRIEND if self._private else self.GROUP


def test_non_admin_denied_for_new_commands():
    settings = load_settings(DummyConfig({"admin_qq_ids": "111"}))
    for cmd in ("list", "view", "ok", "no", "auto", "manual", "record", "off"):
        allowed, msg = can_run_command(settings, cmd, DummyEvent("222"))
        assert not allowed, cmd
        assert msg == "无权限"


def test_non_admin_denied():
    settings = load_settings(DummyConfig({"admin_qq_ids": "111"}))
    allowed, msg = can_run_command(settings, "pending", DummyEvent("222"))
    assert not allowed
    assert msg == "无权限"


def test_debug_mode_status():
    settings = load_settings(DummyConfig({"admin_qq_ids": ""}))
    allowed, _ = can_run_command(settings, "status", DummyEvent("222"))
    assert allowed


def test_debug_mode_pending_denied():
    settings = load_settings(DummyConfig({"admin_qq_ids": ""}))
    allowed, msg = can_run_command(settings, "pending", DummyEvent("222"))
    assert not allowed
    assert "未配置管理员" in msg
