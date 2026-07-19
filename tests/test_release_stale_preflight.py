import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.release import ReleaseService, format_release_result
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _strong_req(req_id: str, *, group_id: str, user_id: str, flag: str) -> PendingRequest:
    sid = f"2612{int(user_id):05d}"[-9:]
    return PendingRequest(
        id=req_id,
        group_id=group_id,
        user_id=user_id,
        comment=f"用户{user_id} {sid}",
        flag=flag,
        sub_type="add",
        parsed={"name": f"用户{user_id}", "student_id": sid},
        match={"strength": "strong"},
        decision="approve",
        confidence=0.95,
        reason="strong",
        mode="record-only",
        status="pending",
        created_at=f"2026-07-19T00:00:0{user_id[-1]}+00:00",
        match_strength="strong",
    )


def _entry(group_id: str, user_id: str, flag: str, comment: str | None = None) -> dict:
    return {
        "group_id": group_id,
        "requester_uin": user_id,
        "flag": flag,
        "request_id": flag.split(":")[2] if flag.startswith("slreq:") else flag,
        "comment": comment or f"用户{user_id}",
    }


async def _pipeline(tmp_path, settings, actions):
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    notifier.notify_stale_request = AsyncMock()
    notifier.settings = settings
    return (
        AuditPipeline(settings, requests, audit, runtime, cache, actions, notifier),
        requests,
        audit,
        runtime,
        notifier,
    )


@pytest.mark.asyncio
async def test_release_preflight_stale_external_refresh_ambiguous_and_saturated(tmp_path):
    group_a = "796836121"
    group_b = "796836122"
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": f"{group_a},{group_b}",
                "batch_approve_max_count": 10,
                "batch_approve_interval_ms": 0,
                "admin_notify": True,
            }
        )
    )
    actions = MagicMock()
    live_new_flag = f"slreq:add:new-refresh:{group_a}:token"
    normal_flag = "normal-flag"
    saturated_flag = "saturated-legacy"
    group_a_snapshot = [
        _entry(group_a, "333", live_new_flag, "用户333 261200333"),
        _entry(group_a, "666", normal_flag, "用户666 261200666"),
        _entry(group_a, "555", "amb-1", "ambiguous"),
        _entry(group_a, "555", "amb-2", "ambiguous"),
    ]
    group_b_snapshot = [
        _entry(group_b, str(9000 + i), f"sat-{i}") for i in range(20)
    ]

    async def get_group_system_msg(group_id=None, *, no_cache=True):
        if group_id == group_b:
            return ActionResult(ok=True, data=group_b_snapshot)
        return ActionResult(ok=True, data=group_a_snapshot)

    async def get_group_member_info(group_id, user_id, *, no_cache=True):
        if user_id == "222":
            return ActionResult(ok=True, data={"user_id": user_id})
        return ActionResult(ok=False, message="not found")

    actions.get_group_system_msg = AsyncMock(side_effect=get_group_system_msg)
    actions.get_group_member_info = AsyncMock(side_effect=get_group_member_info)
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, runtime, _ = await _pipeline(tmp_path, settings, actions)
    reqs = [
        _strong_req("REQ-stale", group_id=group_a, user_id="111", flag="stale-legacy"),
        _strong_req("REQ-external", group_id=group_a, user_id="222", flag="external-legacy"),
        _strong_req("REQ-refresh", group_id=group_a, user_id="333", flag="refresh-legacy"),
        _strong_req("REQ-saturated", group_id=group_b, user_id="444", flag=saturated_flag),
        _strong_req("REQ-ambiguous", group_id=group_a, user_id="555", flag="ambiguous-legacy"),
        _strong_req("REQ-normal", group_id=group_a, user_id="666", flag=normal_flag),
        _strong_req("REQ-unseen", group_id=group_a, user_id="777", flag="unseen-legacy"),
    ]
    for req in reqs:
        await requests.upsert(req)
    await runtime.save_qq_snapshot_index(
        group_a,
        {
            "flags": ["stale-legacy", "external-legacy"],
            "user_keys": [f"{group_a}:111", f"{group_a}:222"],
            "request_ids": [],
        },
    )

    result = await ReleaseService().run_batch(
        requests_store=requests,
        pipeline=pipe,
        settings=settings,
        admin_user_id="admin",
        count=10,
        audit_log=audit,
        skip_rematch=True,
    )

    assert result is not None
    assert (await requests.get_by_id("REQ-stale")).status == "stale"
    assert (await requests.get_by_id("REQ-external")).status == "external"
    assert (await requests.get_by_id("REQ-ambiguous")).status == "pending"
    assert (await requests.get_by_id("REQ-unseen")).status == "processed"
    refreshed = await requests.get_by_id("REQ-refresh")
    assert refreshed.flag == live_new_flag
    assert result.stale_count == 1
    assert result.external_count == 1
    assert result.failed == 0
    assert {line.final_status for line in result.lines} >= {
        "stale",
        "external",
        "skipped",
        "success",
    }
    called_flags = [call.args[0] for call in actions.set_group_add_request.await_args_list]
    assert called_flags[0] == live_new_flag
    assert set(called_flags) == {
        live_new_flag,
        saturated_flag,
        normal_flag,
        "unseen-legacy",
    }
    assert "stale-legacy" not in called_flags
    assert "external-legacy" not in called_flags
    assert "ambiguous-legacy" not in called_flags
    assert any(r.get("type") == "batch_preflight_flag_refreshed" for r in audit.read_all())
    text = format_release_result(result, settings)
    assert "已失效" in text
    assert "外部" in text
