from __future__ import annotations

import inspect
from typing import Any


async def invoke_probe_api(actions: Any, event: Any | None = None) -> dict[str, Any]:
    probe_api = getattr(actions, "probe_api", None)
    if not callable(probe_api):
        return {
            "adapter_found": "no",
            "adapter_action_available": "no",
            "test_action": "",
            "result": "failed",
            "message": "probe_api not available on action client",
        }
    try:
        params = inspect.signature(probe_api).parameters
        if event is not None and len(params) > 1:
            return await probe_api(event)
        return await probe_api()
    except TypeError:
        return await probe_api()
