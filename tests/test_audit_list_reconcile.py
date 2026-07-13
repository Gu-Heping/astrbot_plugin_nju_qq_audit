"""Tests for /audit list automatic QQ-side pending reconciliation."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.ux_formatter import format_list
from config import load_settings
from core.pending_reconcile import PendingReconcileSummary
from core.pipeline import AuditPipeline
from data_source.students import ActionResult, PendingRequest
from storage.audit_log import AuditLog
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore, new_request_id
from storage.runtime_store import RuntimeStore

GROUP_ID = "796836121"
USER_ID = "2492835361"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _pending(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment="张三20260002",
        flag="flag-list-1",
        sub_type="add",
        parsed={"name": "张三"},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="待人工",
        mode="record-only",
        status="pending",
        created_at="2026-07-13T04:00:00+00:00",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _qq_payload(*entries: dict) -> dict:
    return {"join_requests": list(entries)}


def _pipeline(tmp_path, actions: MagicMock, *, timeout_ms: int = 4000):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP_ID,
                "admin_notify": True,
                "audit_list_reconcile_timeout_ms": timeout_ms,
                "audit_list_reject_wait_seconds": 0,
                "audit_list_reject_confirm_snapshots": 2,
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = MagicMock()
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    return pipe, requests, audit, list_cache, notifier


async def _seed_snapshot(runtime: RuntimeStore, *, flags: list[str]) -> None:
    await runtime.save_qq_snapshot_index(
        GROUP_ID,
        {
            "flags": flags,
            "user_keys": [f"{GROUP_ID}:{USER_ID}"],
            "request_ids": [],
        },
    )


@pytest.mark.asyncio
async def test_external_approve_clears_pending_on_list_reconcile(tmp_path):
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        side_effect=[
            ActionResult(ok=True, data=_qq_payload({"group_id": int(GROUP_ID), "requester_uin": int(USER_ID), "flag": "flag-list-1"})),
            ActionResult(ok=True, data=_qq_payload()),
        ]
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"user_id": int(USER_ID), "nickname": "张三"})
    )
    pipe, requests, audit, list_cache, notifier = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)
    await list_cache.refresh("admin", [req.id])

    first = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    assert first.external_approved == 0

    second = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    assert second.external_approved == 1
    assert len(await requests.list_pending(limit=10)) == 0
    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    assert notifier.notify_external_handled.await_count == 0
    assert any(r.get("type") == "external_approved" for r in audit.read_all())


@pytest.mark.asyncio
async def test_external_reject_inferred_requires_multi_snapshot(tmp_path):
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        side_effect=[
            ActionResult(ok=True, data=_qq_payload({"group_id": int(GROUP_ID), "requester_uin": int(USER_ID), "flag": "flag-list-1"})),
            ActionResult(ok=True, data=_qq_payload()),
            ActionResult(ok=True, data=_qq_payload()),
        ]
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)

    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    first_absent = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    assert first_absent.external_rejected_inferred == 0
    assert len(await requests.list_pending(limit=10)) == 1
    assert first_absent.external_handled_unknown == 1

    second_absent = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    assert second_absent.external_rejected_inferred == 1
    updated = await requests.get_by_id(req.id)
    assert updated.status == "processed"
    assert updated.decision == "reject"
    assert any(r.get("type") == "external_rejected_inferred" for r in audit.read_all())


@pytest.mark.asyncio
async def test_single_empty_after_seen_does_not_reject(tmp_path):
    """SnowLuma may return [] on internal failure; one absence must not reject."""
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        side_effect=[
            ActionResult(ok=True, data=_qq_payload({"group_id": int(GROUP_ID), "requester_uin": int(USER_ID), "flag": "flag-list-1"})),
            ActionResult(ok=True, data=_qq_payload()),
        ]
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    pipe, requests, _, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)

    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    assert summary.external_rejected_inferred == 0
    assert len(await requests.list_pending(limit=10)) == 1
    assert summary.snowluma_empty_ambiguity is True


@pytest.mark.asyncio
async def test_failed_empty_query_does_not_clear_pending(tmp_path):
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        return_value=ActionResult(ok=False, message="backend unavailable")
    )
    actions.get_group_member_info = AsyncMock()
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)
    await _seed_snapshot(runtime=pipe.runtime, flags=[req.flag])

    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    assert summary.failed is True
    assert len(await requests.list_pending(limit=10)) == 1
    assert any(r.get("type") == "reconcile_failed" for r in audit.read_all())
    actions.get_group_member_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_empty_snapshot_does_not_clear(tmp_path):
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        return_value=ActionResult(ok=True, data=_qq_payload())
    )
    actions.get_group_member_info = AsyncMock()
    pipe, requests, _, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)

    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    assert summary.failed is False
    assert len(await requests.list_pending(limit=10)) == 1
    actions.get_group_member_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_match_does_not_clear(tmp_path):
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        return_value=ActionResult(
            ok=True,
            data=_qq_payload(
                {
                    "group_id": int(GROUP_ID),
                    "requester_uin": int(USER_ID),
                    "flag": "flag-list-1",
                    "message": "comment-a",
                },
                {
                    "group_id": int(GROUP_ID),
                    "requester_uin": 1111111111,
                    "flag": "flag-list-1",
                    "message": "comment-b",
                },
            ),
        )
    )
    pipe, requests, _, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)

    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    assert summary.skipped_ambiguous == 1
    assert len(await requests.list_pending(limit=10)) == 1


@pytest.mark.asyncio
async def test_member_query_failure_keeps_pending(tmp_path):
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        side_effect=[
            ActionResult(ok=True, data=_qq_payload({"group_id": int(GROUP_ID), "requester_uin": int(USER_ID), "flag": "flag-list-1"})),
            ActionResult(ok=True, data=_qq_payload()),
        ]
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="timeout")
    )
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)

    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    assert summary.external_handled_unknown == 1
    assert len(await requests.list_pending(limit=10)) == 1
    assert any(r.get("type") == "external_handled_unknown" for r in audit.read_all())


@pytest.mark.asyncio
async def test_reconcile_is_idempotent(tmp_path):
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        side_effect=[
            ActionResult(ok=True, data=_qq_payload({"group_id": int(GROUP_ID), "requester_uin": int(USER_ID), "flag": "flag-list-1"})),
            ActionResult(ok=True, data=_qq_payload()),
            ActionResult(ok=True, data=_qq_payload()),
        ]
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"user_id": int(USER_ID), "nickname": "张三"})
    )
    pipe, requests, audit, list_cache, notifier = _pipeline(tmp_path, actions)
    req = _pending()
    await requests.upsert(req)

    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    approved = [r for r in audit.read_all() if r.get("type") == "external_approved"]
    assert len(approved) == 1
    assert notifier.notify_external_handled.await_count == 0


@pytest.mark.asyncio
async def test_reconcile_timeout_keeps_pending_and_shows_failure(tmp_path):
    async def slow_system_msg(group_id):
        await asyncio.sleep(0.2)
        return ActionResult(ok=True, data=_qq_payload())

    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(side_effect=slow_system_msg)
    actions.get_group_member_info = AsyncMock()
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path, actions)
    # Bypass config clamp so the timeout path is testable without a long sleep.
    pipe.settings.audit_list_reconcile_timeout_ms = 50
    req = _pending()
    await requests.upsert(req)

    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    assert summary.failed is True
    assert len(await requests.list_pending(limit=10)) == 1
    assert any(r.get("type") == "reconcile_failed" for r in audit.read_all())
    text = format_list(
        await requests.list_pending(limit=10),
        {1: req.id},
        reconcile_summary=summary,
    )
    assert "QQ 状态同步失败，本次展示本地队列" in text


@pytest.mark.asyncio
async def test_format_list_includes_sync_summary(tmp_path):
    summary = PendingReconcileSummary(
        external_approved=1,
        external_rejected_inferred=1,
        external_handled_unknown=0,
    )
    text = format_list([], {}, reconcile_summary=summary)
    assert "本次自动同步" in text
    assert "外部同意：1" in text
    assert "外部拒绝（推断）：1" in text


@pytest.mark.asyncio
async def test_saturated_snapshot_does_not_reject_missing_pending(tmp_path):
    """Previously seen A missing from a full 20-item snapshot must stay pending."""
    flag_a = "flag-list-1"
    first_payload = [
        {
            "group_id": int(GROUP_ID),
            "requester_uin": int(USER_ID),
            "flag": flag_a,
            "message": "A",
        }
    ]
    saturated = [
        {
            "group_id": int(GROUP_ID),
            "requester_uin": 1000000000 + i,
            "flag": f"flag-other-{i}",
            "message": f"other-{i}",
        }
        for i in range(20)
    ]
    assert len(saturated) == 20
    assert not any(item["flag"] == flag_a for item in saturated)

    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        side_effect=[
            ActionResult(ok=True, data=first_payload),
            ActionResult(ok=True, data=saturated),
        ]
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending(flag=flag_a)
    await requests.upsert(req)

    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    assert summary.snapshot_saturated is True
    assert summary.external_rejected_inferred == 0
    assert summary.absence_not_trusted == 1
    assert len(await requests.list_pending(limit=10)) == 1
    assert (await requests.get_by_id(req.id)).status == "pending"
    records = audit.read_all()
    assert any(r.get("type") == "reconcile_snapshot_saturated" for r in records)
    assert any(r.get("type") == "reconcile_absence_not_trusted" for r in records)
    text = format_list(
        await requests.list_pending(limit=10),
        {1: req.id},
        reconcile_summary=summary,
    )
    assert "SnowLuma 返回达到 20 条上限，未对缺失申请进行拒绝推断。" in text


@pytest.mark.asyncio
async def test_saturated_snapshot_still_allows_member_approved(tmp_path):
    flag_a = "flag-list-1"
    first_payload = [
        {
            "group_id": int(GROUP_ID),
            "requester_uin": int(USER_ID),
            "flag": flag_a,
            "message": "A",
        }
    ]
    saturated = [
        {
            "group_id": int(GROUP_ID),
            "requester_uin": 1000000000 + i,
            "flag": f"flag-other-{i}",
            "message": f"other-{i}",
        }
        for i in range(20)
    ]
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        side_effect=[
            ActionResult(ok=True, data=first_payload),
            ActionResult(ok=True, data=saturated),
        ]
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"user_id": int(USER_ID), "nickname": "在群"})
    )
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path, actions)
    req = _pending(flag=flag_a)
    await requests.upsert(req)

    await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)
    summary = await pipe.reconcile_active_pending(source="audit_list", list_cache=list_cache)

    assert summary.external_approved == 1
    assert summary.external_rejected_inferred == 0
    assert (await requests.get_by_id(req.id)).status == "external"
    assert any(r.get("type") == "reconcile_snapshot_saturated" for r in audit.read_all())
