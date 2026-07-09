from config import load_settings, validate_settings


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_default_action_backend_and_empty_http_url():
    settings = load_settings(DummyConfig())
    assert settings.onebot_action_backend == "astrbot_adapter"
    assert settings.onebot_http_url == ""


def test_http_backend_without_url_warns():
    settings = load_settings(DummyConfig({"onebot_action_backend": "http"}))
    warnings = validate_settings(settings)
    assert any("onebot_http_url" in w for w in warnings)


def test_http_backend_with_url_no_warning():
    settings = load_settings(
        DummyConfig(
            {
                "onebot_action_backend": "http",
                "onebot_http_url": "http://127.0.0.1:3000",
            }
        )
    )
    assert validate_settings(settings) == []
