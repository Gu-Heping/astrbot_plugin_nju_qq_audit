from unittest.mock import MagicMock

from onebot.platform_cache import cache_event_platform


def test_cache_event_platform_sets_bot_and_platform_id():
    ctx = MagicMock()
    ctx.actions = MagicMock()
    event = MagicMock()
    event.bot = MagicMock()
    event.bot.api = MagicMock()
    event.get_platform_id.return_value = "aiocqhttp_1"

    cache_event_platform(ctx, event)

    assert ctx._event_bot is event.bot
    assert ctx._platform_id == "aiocqhttp_1"
    assert ctx.actions._event_bot is event.bot
    assert ctx.actions._platform_id == "aiocqhttp_1"
