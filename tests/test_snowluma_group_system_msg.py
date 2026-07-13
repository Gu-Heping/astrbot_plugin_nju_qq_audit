"""SnowLuma get_group_system_msg shape + real adapter normalize chain."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from config import load_settings
from core.pipeline import AuditPipeline
from data_source.students import ActionResult, PendingRequest
from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient
from onebot.group_system_msg import (
    describe_group_system_msg_result,
    match_pending_to_entries,
    parse_group_system_msg_data,
    parse_slreq_flag,
)
from storage.audit_log import AuditLog
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore, new_request_id
from storage.runtime_store import RuntimeStore

GROUP_ID = "796836121"
USER_ID = "2492835361"
SNOWLUMA_FLAG = "slreq:1:123:796836121:7:0"

SNOWLUMA_RESPONSE = {
    "status": "ok",
    "retcode": 0,
    "data": [
        {
            "group_id": 796836121,
            "group_name": "测试群",
            "request_id": 123,
            "requester_uin": 0,
            "requester_nick": "测试",
            "message": "测试",
            "flag": SNOWLUMA_FLAG,
        }
    ],
}


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_normalize_preserves_snowluma_list_data():
    settings = load_settings(DummyConfig())
    client = AstrBotAdapterActionClient(MagicMock(), settings)
    result = client._normalize_response("get_group_system_msg", SNOWLUMA_RESPONSE)
    assert result.ok is True
    assert isinstance(result.data, list)
    assert len(result.data) == 1
    assert result.data[0]["flag"] == SNOWLUMA_FLAG


def test_normalize_preserves_aiocqhttp_unwrapped_list():
    """aiocqhttp may return action data already unwrapped as a bare list."""
    settings = load_settings(DummyConfig())
    client = AstrBotAdapterActionClient(MagicMock(), settings)
    unwrapped = [
        {
            "flag": SNOWLUMA_FLAG,
            "group_id": 796836121,
            "request_id": 123,
            "requester_uin": 0,
            "message": "测试",
        }
    ]
    result = client._normalize_response("get_group_system_msg", unwrapped)
    assert result.ok is True
    assert result.retcode == 0
    assert isinstance(result.data, list)
    assert result.data[0]["flag"] == SNOWLUMA_FLAG


def test_normalize_empty_list_is_list_not_none():
    settings = load_settings(DummyConfig())
    client = AstrBotAdapterActionClient(MagicMock(), settings)
    for response in ([], {"status": "ok", "retcode": 0, "data": []}):
        result = client._normalize_response("get_group_system_msg", response)
        assert result.ok is True
        assert isinstance(result.data, list)
        assert result.data == []
        probe = describe_group_system_msg_result(result)
        assert probe["data_type"] == "list"
        assert probe["top_level_shape"] == "list"
        assert probe["parser_variant"] == "snowluma_list"
        assert probe["group_system_msg_action_available"] == "yes"


def test_parse_snowluma_top_level_list():
    parsed = parse_group_system_msg_data(SNOWLUMA_RESPONSE["data"])
    assert parsed.variant == "snowluma_list"
    assert parsed.top_level_shape == "list"
    assert parsed.request_count == 1
    assert parsed.entries[0].flag == SNOWLUMA_FLAG
    assert parsed.entries[0].requester_uin == "0"
    assert parsed.entries[0].request_id == "123"


def test_parse_napcat_join_requests_still_works():
    parsed = parse_group_system_msg_data(
        {
            "join_requests": [
                {
                    "group_id": GROUP_ID,
                    "requester_uin": USER_ID,
                    "flag": "flag-1",
                    "message": "hi",
                }
            ]
        }
    )
    assert parsed.variant == "napcat_dict"
    assert parsed.request_count == 1


def test_requester_uin_zero_does_not_block_flag_match():
    parsed = parse_group_system_msg_data(SNOWLUMA_RESPONSE["data"])
    match = match_pending_to_entries(
        flag=SNOWLUMA_FLAG,
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment="测试",
        entries=parsed.entries,
    )
    assert match.kind == "unique"
    assert match.match_by == "flag"
    assert match.entry is not None
    assert match.entry.requester_uin == "0"


def test_slreq_flag_request_id_match_when_flag_differs_suffix():
    request_id, group_id = parse_slreq_flag(SNOWLUMA_FLAG)
    assert request_id == "123"
    assert group_id == "796836121"
    parsed = parse_group_system_msg_data(SNOWLUMA_RESPONSE["data"])
    match = match_pending_to_entries(
        flag="slreq:1:123:796836121:9:1",
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment="",
        entries=parsed.entries,
    )
    assert match.kind == "unique"
    assert match.match_by == "slreq_request_id"


def test_requester_uin_zero_not_used_as_user_match():
    parsed = parse_group_system_msg_data(SNOWLUMA_RESPONSE["data"])
    match = match_pending_to_entries(
        flag="",
        group_id=GROUP_ID,
        user_id="0",
        comment="",
        entries=parsed.entries,
    )
    assert match.kind == "none"


def test_describe_probe_fields_sanitized():
    settings = load_settings(DummyConfig())
    client = AstrBotAdapterActionClient(MagicMock(), settings)
    result = client._normalize_response("get_group_system_msg", SNOWLUMA_RESPONSE)
    probe = describe_group_system_msg_result(result)
    assert probe["action_status"] == "ok"
    assert probe["data_type"] == "list"
    assert probe["request_count"] == 1
    assert probe["top_level_shape"] == "list"
    assert probe["parser_variant"] == "snowluma_list"
    assert "flag" in probe["first_request_fields"]


@pytest.mark.asyncio
async def test_real_call_chain_normalize_get_parse_reconcile(tmp_path):
    """aiocqhttp response → normalize → get_group_system_msg → parser → reconcile."""
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP_ID,
                "admin_notify": False,
                "audit_list_reject_wait_seconds": 0,
            }
        )
    )
    context = MagicMock()
    client = AstrBotAdapterActionClient(context, settings)
    bot = MagicMock()
    bot.api.call_action = AsyncMock(return_value=SNOWLUMA_RESPONSE)

    async def fake_get_bot(event=None):
        return bot

    client._get_bot_client = fake_get_bot

    # Step 1-2: get_group_system_msg through normalize
    result = await client.get_group_system_msg(GROUP_ID)
    assert result.ok is True
    assert isinstance(result.data, list)
    assert result.data[0]["flag"] == SNOWLUMA_FLAG

    # Step 3: parser
    parsed = parse_group_system_msg_data(result.data)
    assert parsed.variant == "snowluma_list"
    assert parsed.request_count == 1

    # Step 4: reconcile keeps pending when still present (flag match)
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    pipe = AuditPipeline(
        settings, requests, audit, runtime, MagicMock(), client, MagicMock()
    )
    req = PendingRequest(
        id=new_request_id(),
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment="测试",
        flag=SNOWLUMA_FLAG,
        sub_type="add",
        parsed={},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="待人工",
        mode="record-only",
        status="pending",
        created_at="2026-07-13T04:00:00+00:00",
    )
    await requests.upsert(req)
    summary = await pipe.reconcile_active_pending(
        source="audit_list",
        list_cache=AdminListCacheStore(tmp_path / "list_cache.json"),
    )
    assert summary.failed is False
    assert summary.unchanged == 1
    assert (await requests.get_by_id(req.id)).status == "pending"
    bot.api.call_action.assert_awaited()


@pytest.mark.asyncio
async def test_unwrapped_list_call_chain_parser_variant_snowluma(tmp_path):
    """Bare list from aiocqhttp must reach snowluma_list parser via get_group_system_msg."""
    settings = load_settings(
        DummyConfig({"target_group_ids": GROUP_ID, "admin_notify": False})
    )
    client = AstrBotAdapterActionClient(MagicMock(), settings)
    unwrapped = [
        {
            "group_id": 796836121,
            "request_id": 123,
            "requester_uin": 0,
            "message": "测试",
            "flag": SNOWLUMA_FLAG,
        }
    ]
    bot = MagicMock()
    bot.api.call_action = AsyncMock(return_value=unwrapped)

    async def fake_get_bot(event=None):
        return bot

    client._get_bot_client = fake_get_bot

    result = await client.get_group_system_msg(GROUP_ID)
    assert isinstance(result.data, list)
    probe = describe_group_system_msg_result(result)
    assert probe["parser_variant"] == "snowluma_list"
    assert probe["data_type"] == "list"

    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    pipe = AuditPipeline(
        settings, requests, audit, runtime, MagicMock(), client, MagicMock()
    )
    await requests.upsert(
        PendingRequest(
            id=new_request_id(),
            group_id=GROUP_ID,
            user_id=USER_ID,
            comment="测试",
            flag=SNOWLUMA_FLAG,
            sub_type="add",
            parsed={},
            match={},
            decision="manual_review",
            confidence=0.5,
            reason="待人工",
            mode="record-only",
            status="pending",
            created_at="2026-07-13T04:00:00+00:00",
        )
    )
    summary = await pipe.reconcile_active_pending(
        source="audit_list",
        list_cache=AdminListCacheStore(tmp_path / "list_cache.json"),
    )
    assert summary.failed is False
    assert summary.unchanged == 1


def test_debug_shows_adapter_found_and_gsm_available():
    from admin.formatter import format_status
    from data_source.student_cache import SyncState

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
        adapter_probe={"adapter_found": "no", "adapter_action_available": "no"},
        group_system_msg_probe={
            "action_status": "ok",
            "retcode": 0,
            "data_type": "list",
            "request_count": 0,
            "top_level_shape": "list",
            "first_request_fields": "",
            "parser_variant": "snowluma_list",
            "group_system_msg_action_available": "yes",
        },
    )
    assert "adapter_found: yes" in text
    assert "group_system_msg_action_available: yes" in text
    assert "adapter_action_available: no" not in text
    assert "parser_variant: snowluma_list" in text
    assert "data_type: list" in text
    assert "NoneType" not in text
    assert "parse_failed" not in text
