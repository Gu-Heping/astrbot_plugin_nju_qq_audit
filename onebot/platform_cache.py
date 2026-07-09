from __future__ import annotations

from typing import Any


def cache_event_platform(ctx: Any, event: Any) -> None:
    """Cache event.bot / platform_id on ctx and actions. Safe across hot-reload."""
    bot = getattr(event, "bot", None)
    platform_id = None
    getter = getattr(event, "get_platform_id", None)
    if callable(getter):
        try:
            platform_id = getter()
        except Exception:
            platform_id = None
    for target in (getattr(ctx, "actions", None), ctx):
        if target is None:
            continue
        if bot is not None and hasattr(bot, "api"):
            target._event_bot = bot
        if platform_id:
            target._platform_id = str(platform_id)
