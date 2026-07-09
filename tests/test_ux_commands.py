from admin.permissions import can_run_command
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


def test_non_admin_denied_for_ok():
    settings = load_settings(DummyConfig({"admin_qq_ids": "111"}))
    allowed, msg = can_run_command(settings, "ok", DummyEvent("222"))
    assert not allowed
    assert msg == "无权限"


def test_non_admin_denied_for_list():
    settings = load_settings(DummyConfig({"admin_qq_ids": "111"}))
    allowed, _ = can_run_command(settings, "list", DummyEvent("222"))
    assert not allowed


def test_group_chat_denied():
    settings = load_settings(DummyConfig({"admin_qq_ids": "111"}))
    allowed, msg = can_run_command(settings, "list", DummyEvent("111", private=False))
    assert not allowed
    assert msg == "请私聊机器人使用"


def test_admin_allowed_for_view():
    settings = load_settings(DummyConfig({"admin_qq_ids": "111"}))
    allowed, msg = can_run_command(settings, "view", DummyEvent("111"))
    assert allowed
    assert msg == ""


def test_debug_allowed_without_admin_config():
    settings = load_settings(DummyConfig({"admin_qq_ids": ""}))
    allowed, _ = can_run_command(settings, "debug", DummyEvent("222"))
    assert allowed


def test_home_allowed_without_admin_config():
    settings = load_settings(DummyConfig({"admin_qq_ids": ""}))
    allowed, _ = can_run_command(settings, "home", DummyEvent("222"))
    assert allowed
