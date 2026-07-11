import asyncio

import pytest

from admin.command_resolver import (
    map_action_error,
    parse_no_command_reason,
    resolve_request_ref,
)
from data_source.students import PendingRequest
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore, new_request_id


def _pending(req_id: str, *, status="pending", processed_at=None) -> PendingRequest:
    return PendingRequest(
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


def test_map_action_error_hides_raw():
    assert "adapter" not in map_action_error("aiocqhttp adapter not available")
    assert "审批接口" in map_action_error("aiocqhttp adapter not available")
    assert "其他管理员" in map_action_error("flag expired")


def test_parse_no_command_reason():
    reason = parse_no_command_reason("/audit no 2 信息不完整", "2")
    assert reason == "信息不完整"


def test_parse_no_command_default_reason():
    reason = parse_no_command_reason("/audit no 2", "2")
    assert "学号" in reason
