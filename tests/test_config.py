from config import load_settings, get_effective_mode, mask_secret
from storage.runtime_store import RuntimeStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_load_settings_defaults():
    settings = load_settings(DummyConfig())
    assert settings.mode == "record-only"
    assert settings.student_source == "mock"
    assert settings.njutable_page_size == 1000
    assert settings.onebot_action_backend == "astrbot_adapter"
    assert settings.onebot_http_url == ""


def test_runtime_mode_override(tmp_path):
    settings = load_settings(DummyConfig({"mode": "record-only"}))
    runtime = RuntimeStore(tmp_path / "runtime.json")
    assert get_effective_mode(settings, runtime.get_mode_override()) == ("record-only", "plugin_config")
    import asyncio

    asyncio.run(runtime.set_mode("auto", "123"))
    assert get_effective_mode(settings, runtime.get_mode_override()) == ("auto", "runtime")


def test_target_group_ids_parse():
    settings = load_settings(DummyConfig({"target_group_ids": "1093442531,abc,222"}))
    assert settings.target_group_ids == frozenset({"1093442531", "222"})


def test_mask_secret():
    assert mask_secret("abcdefghij") == "abcd***"


def test_page_size_clamp():
    settings = load_settings(DummyConfig({"njutable_page_size": 5000}))
    assert settings.njutable_page_size == 1000
