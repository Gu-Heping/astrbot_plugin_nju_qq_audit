import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from onebot.compat import invoke_probe_api


@pytest.mark.asyncio
async def test_invoke_probe_api_with_event_param():
    actions = MagicMock()
    actions.probe_api = AsyncMock(return_value={"result": "ok", "adapter_found": "yes"})
    result = await invoke_probe_api(actions, MagicMock())
    actions.probe_api.assert_awaited_once()
    assert result["result"] == "ok"


@pytest.mark.asyncio
async def test_invoke_probe_api_without_event_param():
    async def legacy_probe_api():
        return {"result": "legacy", "adapter_found": "yes"}

    actions = MagicMock()
    actions.probe_api = legacy_probe_api
    result = await invoke_probe_api(actions, MagicMock())
    assert result["result"] == "legacy"
