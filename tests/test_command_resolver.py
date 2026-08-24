import asyncio

import pytest

from admin.command_resolver import (
    map_action_error,
    parse_dismiss_command,
    parse_no_command_reason,
    resolve_request_ref,
)
from data_source.students import PendingRequest
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore, new_request_id


def _pending(req_id: str, *, status="pending", processed_at=None, **kwargs) -> PendingRequest:
    defaults = dict(
        id=req_id,
        group_id="796836121",
        user_id="2492835361",
        comment="李四 计算机类",
        flag="secret-flag",
        sub_type="add",
        parsed={"name": "李四"},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="弱匹配",
        mode="record-only",
        status=status,
        created_at="2026-07-09T00:00:00+00:00",
        processed_at=processed_at,
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


async def _setup(tmp_path, req_id: str, **kwargs):
    requests = RequestsStore(tmp_path / "requests.json")
    req = _pending(req_id, **kwargs)
    await requests.upsert(req)
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    await cache.refresh("111", [req_id])
    return requests, cache, req


@pytest.mark.asyncio
async def test_resolve_by_numeric_index(tmp_path):
    req_id = new_request_id()
    requests, cache, req = await _setup(tmp_path, req_id)
    result = await resolve_request_ref("111", "1", list_cache=cache, requests=requests)
    assert result.ok
    assert result.request.id == req_id
    assert result.index == 1


@pytest.mark.asyncio
async def test_resolve_by_req_prefix(tmp_path):
    req_id = new_request_id()
    requests, cache, _ = await _setup(tmp_path, req_id)
    result = await resolve_request_ref("111", req_id[:8], list_cache=cache, requests=requests)
    assert result.ok
    assert result.request.id == req_id


@pytest.mark.asyncio
async def test_resolve_by_qq_when_index_expired(tmp_path):
    req_id = new_request_id()
    requests = RequestsStore(tmp_path / "requests.json")
    await requests.upsert(_pending(req_id, user_id="123456789"))
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    result = await resolve_request_ref(
        "111",
        "qq 123456789",
        list_cache=cache,
        requests=requests,
    )
    assert result.ok
    assert result.request.id == req_id
    assert result.index is None


@pytest.mark.asyncio
async def test_resolve_by_qq_rejects_multiple_pending(tmp_path):
    requests = RequestsStore(tmp_path / "requests.json")
    await requests.upsert(_pending("REQ-one", user_id="123456789"))
    await requests.upsert(_pending("REQ-two", user_id="123456789"))
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    result = await resolve_request_ref(
        "111",
        "qq 123456789",
        list_cache=cache,
        requests=requests,
    )
    assert not result.ok
    assert "2 条待处理申请" in result.message


@pytest.mark.asyncio
async def test_expired_index(tmp_path):
    req_id = new_request_id()
    requests = RequestsStore(tmp_path / "requests.json")
    await requests.upsert(_pending(req_id))
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    result = await resolve_request_ref("111", "1", list_cache=cache, requests=requests)
    assert not result.ok
    assert result.error == "expired_index"
    assert "失效" in result.message


@pytest.mark.asyncio
async def test_not_found(tmp_path):
    requests = RequestsStore(tmp_path / "requests.json")
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    result = await resolve_request_ref("111", "REQ-missing", list_cache=cache, requests=requests)
    assert not result.ok
    assert result.error == "not_found"


@pytest.mark.asyncio
async def test_already_processed(tmp_path):
    req_id = new_request_id()
    requests, cache, _ = await _setup(
        tmp_path,
        req_id,
        status="processed",
        processed_at="2026-07-09T01:00:00+00:00",
    )
    result = await resolve_request_ref("111", "1", list_cache=cache, requests=requests)
    assert not result.ok
    assert result.error == "already_processed"


@pytest.mark.asyncio
async def test_failed_request_allows_retry(tmp_path):
    req_id = new_request_id()
    requests, cache, _ = await _setup(
        tmp_path,
        req_id,
        status="failed",
        processed_at="2026-07-09T01:00:00+00:00",
    )
    result = await resolve_request_ref("111", "1", list_cache=cache, requests=requests)
    assert result.ok
    assert result.request.status == "pending"


def test_map_action_error_hides_raw():
    assert "adapter" not in map_action_error("aiocqhttp adapter not available")
    assert "审批接口" in map_action_error("aiocqhttp adapter not available")
    assert "其他管理员" in map_action_error("flag expired")


def test_parse_no_command_reason():
    reason = parse_no_command_reason("/audit no 2 信息不完整", "2")
    assert reason == "信息不完整"


def test_parse_no_command_reason_with_trailing_confirm():
    reason = parse_no_command_reason("/audit no 3 不可加入多个群 confirm", "3")
    assert reason == "不可加入多个群"


def test_parse_no_command_reason_confirm_before_reason():
    reason = parse_no_command_reason("/audit no 3 confirm 不可加入多个群", "3")
    assert reason == "不可加入多个群"


def test_parse_no_command_default_reason():
    reason = parse_no_command_reason("/audit no 2", "2")
    assert "学号" in reason


def test_parse_no_command_reason_with_qq_ref():
    reason = parse_no_command_reason(
        "/audit no qq 123456789 信息不完整，请重填",
        "qq 123456789",
    )
    assert reason == "信息不完整，请重填"


def test_parse_dismiss_command():
    ok, reason = parse_dismiss_command("/audit dismiss 3 confirm 过期申请", "3")
    assert ok is True
    assert reason == "过期申请"
    ok, reason = parse_dismiss_command("/audit dismiss 3 过期申请", "3")
    assert ok is False
    ok, reason = parse_dismiss_command("/audit dismiss 3 confirm", "3")
    assert ok is True
    assert reason == ""
