"""Tests for QQ lookup across pending store and audit history."""

from __future__ import annotations

import pytest

from admin.formatter import format_lookup_qq_result
from admin.lookup_qq import lookup_qq_records
from data_source.students import PendingRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from tests.test_lookup_qq import _req, _settings


async def _remove_from_store(requests: RequestsStore, req_id: str) -> None:
    async with requests._lock:
        store = requests._read_unlocked()
        store["by_id"].pop(req_id, None)
        requests._write(store)


async def _append_decision(
    audit: AuditLog,
    req_id: str,
    *,
    user_id: str = "123456789",
    group_id: str = "796836121",
    decision: str = "manual_review",
    reason: str = "弱匹配",
    created_at: str = "2026-07-28T08:00:00+00:00",
    match_strength: str = "weak",
    comment: str = "张三 261220001",
) -> None:
    await audit.append(
        {
            "type": "decision_made",
            "request_id": req_id,
            "group_id": group_id,
            "user_id": user_id,
            "comment": comment,
            "decision": decision,
            "reason": reason,
            "match_strength": match_strength,
            "profile": "undergraduate",
            "time": created_at,
        }
    )


@pytest.fixture
def stores(tmp_path):
    settings = _settings()
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    return settings, requests, audit


@pytest.mark.asyncio
async def test_lookup_qq_history_pending(stores):
    _, requests, audit = stores
    await requests.upsert(_req("req-pending"))
    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 1
    assert result.records[0].source == "pending"
    text = format_lookup_qq_result(result)
    assert "pending" in text
    assert "记录来源：" in text


@pytest.mark.asyncio
async def test_lookup_qq_history_approved_after_store_removal(stores):
    _, requests, audit = stores
    req_id = "req-approved"
    await requests.upsert(
        _req(
            req_id,
            status="processed",
            decision="approve",
            reason="强匹配，建议通过",
        )
    )
    await _append_decision(
        audit,
        req_id,
        decision="approve",
        reason="强匹配，建议通过",
        match_strength="strong",
    )
    await audit.append(
        {
            "type": "admin_command",
            "command": "approve",
            "affected_request_id": req_id,
            "result": "ok",
            "time": "2026-07-28T09:00:00+00:00",
        }
    )
    await _remove_from_store(requests, req_id)

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert result.total == 1
    assert result.records[0].source == "audit"
    assert "approved" in text


@pytest.mark.asyncio
async def test_lookup_qq_history_rejected_after_store_removal(stores):
    _, requests, audit = stores
    req_id = "req-rejected"
    await _append_decision(
        audit,
        req_id,
        decision="reject",
        reason="信息不完整",
    )
    await audit.append(
        {
            "type": "blacklist_rejected",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "reason": "信息不完整",
            "final_status": "processed",
            "time": "2026-07-28T09:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "rejected" in text
    assert "信息不完整" in text
    assert result.records[0].blacklist_hit is True


@pytest.mark.asyncio
async def test_lookup_qq_history_external_after_store_removal(stores):
    _, requests, audit = stores
    req_id = "req-external"
    await _append_decision(audit, req_id)
    await audit.append(
        {
            "type": "external_handled",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "message": "QQ 侧已处理",
            "time": "2026-07-28T09:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "external" in text


@pytest.mark.asyncio
async def test_lookup_qq_history_stale_after_store_removal(stores):
    _, requests, audit = stores
    req_id = "req-stale"
    await _append_decision(audit, req_id)
    await audit.append(
        {
            "type": "request_stale",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "reason": "申请已过期",
            "time": "2026-07-28T09:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "stale" in text
    assert "申请已过期" in text


@pytest.mark.asyncio
async def test_lookup_qq_history_multiple_groups(stores):
    _, requests, audit = stores
    await _append_decision(
        audit,
        "req-a",
        group_id="796836121",
        created_at="2026-07-28T09:00:00+00:00",
    )
    await _append_decision(
        audit,
        "req-b",
        group_id="2601",
        created_at="2026-07-28T08:00:00+00:00",
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 2
    text = format_lookup_qq_result(result)
    assert "796836121" in text
    assert "2601" in text


@pytest.mark.asyncio
async def test_lookup_qq_history_audit_only_without_request(stores):
    _, requests, audit = stores
    req_id = "req-audit-only"
    await _append_decision(
        audit,
        req_id,
        reason="仅 audit 保留",
        created_at="2026-07-28T10:00:00+00:00",
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 1
    assert result.records[0].request_id == req_id
    assert result.records[0].source == "audit"
    text = format_lookup_qq_result(result)
    assert "仅 audit 保留" in text


@pytest.mark.asyncio
async def test_lookup_qq_history_qq_field_compatibility(stores):
    _, requests, audit = stores

    await requests.upsert(
        _req("req-user-id", user_id="123456789", parsed={"name": "A"})
    )
    await audit.append(
        {
            "type": "decision_made",
            "request_id": "req-event-user",
            "target_group_id": "796836121",
            "event": {"user_id": "123456789"},
            "comment": "B 261220002",
            "decision": "manual_review",
            "reason": "弱匹配",
            "time": "2026-07-28T07:00:00+00:00",
        }
    )
    await audit.append(
        {
            "type": "decision_made",
            "request_id": "req-sender-id",
            "group_id": "796836121",
            "sender_id": "123456789",
            "comment": "D 261220004",
            "decision": "manual_review",
            "reason": "弱匹配",
            "time": "2026-07-28T05:00:00+00:00",
        }
    )
    await requests.upsert(
        PendingRequest(
            id="req-parsed-qq",
            group_id="2601",
            user_id="999999999",
            comment="C 261220003",
            flag="flag-parsed",
            sub_type="add",
            parsed={"qq": "123456789", "name": "C"},
            match={"strength": "weak"},
            decision="manual_review",
            confidence=0.5,
            reason="弱匹配",
            mode="record-only",
            status="pending",
            created_at="2026-07-28T06:00:00+00:00",
            match_strength="weak",
        )
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 4
    ids = {item.request_id for item in result.records}
    assert ids == {"req-user-id", "req-event-user", "req-sender-id", "req-parsed-qq"}


@pytest.mark.asyncio
async def test_lookup_qq_history_merges_pending_and_audit_by_request_id(stores):
    _, requests, audit = stores
    req_id = "req-merge"
    await requests.upsert(_req(req_id, reason="store 原因"))
    await _append_decision(audit, req_id, reason="audit 原因")
    await audit.append(
        {
            "type": "admin_command",
            "command": "approve",
            "affected_request_id": req_id,
            "result": "ok",
            "time": "2026-07-28T09:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 1
    assert result.records[0].request_id == req_id
    assert result.records[0].source == "pending"
    assert result.records[0].reason == "audit 原因"
    assert result.records[0].status == "processed"
    assert result.records[0].decision == "approve"


@pytest.mark.asyncio
async def test_lookup_qq_history_no_records(stores):
    _, requests, audit = stores
    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "未找到历史申请记录" in text


@pytest.mark.asyncio
async def test_lookup_qq_history_output_safety(stores):
    settings, requests, audit = stores
    req_id = "req-safe"
    await _append_decision(audit, req_id)
    await audit.append(
        {
            "type": "pending_reparsed",
            "request_id": req_id,
            "user_id": "123456789",
            "flag": "must-not-show",
            "raw_event": {"token": "secret-token"},
            "time": "2026-07-28T09:00:00+00:00",
        }
    )
    await audit.append(
        {
            "type": "undergrad_exclusive_policy_hit",
            "request_id": req_id,
            "user_id": "123456789",
            "hit_group_ids": ["2601"],
            "time": "2026-07-28T09:01:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result, settings=settings)
    lowered = text.lower()
    assert "must-not-show" not in text
    assert "raw_event" not in lowered
    assert "secret-token" not in text
    assert "_internal" not in lowered
    assert "多群互斥" in text


@pytest.mark.asyncio
async def test_lookup_qq_store_approve_audit_reject_shows_rejected(stores):
    _, requests, audit = stores
    req_id = "req-store-approve-audit-reject"
    await requests.upsert(
        _req(
            req_id,
            status="processed",
            decision="approve",
            reason="强匹配，建议通过",
        )
    )
    await audit.append(
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": req_id,
            "reason": "后续人工拒绝",
            "result": "ok",
            "time": "2026-07-28T10:00:00+00:00",
        }
    )

    before = await requests.get_by_id(req_id)
    result = await lookup_qq_records(requests, audit, "123456789")
    after = await requests.get_by_id(req_id)

    assert before is not None and after is not None
    assert before.status == after.status == "processed"
    assert before.decision == after.decision == "approve"
    assert result.records[0].decision == "reject"
    text = format_lookup_qq_result(result)
    assert "rejected" in text
    assert "后续人工拒绝" in text


@pytest.mark.asyncio
async def test_lookup_qq_store_pending_audit_approve_shows_approved(stores):
    _, requests, audit = stores
    req_id = "req-store-pending-audit-approve"
    await requests.upsert(_req(req_id, status="pending", decision="manual_review"))
    await audit.append(
        {
            "type": "admin_command",
            "command": "approve",
            "affected_request_id": req_id,
            "result": "ok",
            "time": "2026-07-28T10:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert result.records[0].status == "processed"
    assert result.records[0].decision == "approve"
    assert "approved" in text


@pytest.mark.asyncio
async def test_lookup_qq_audit_only_external_handled(stores):
    _, requests, audit = stores
    req_id = "req-only-external"
    await audit.append(
        {
            "type": "external_handled",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "message": "仅 external 事件",
            "time": "2026-07-28T10:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert result.total == 1
    assert result.records[0].source == "audit"
    assert "external" in text
    assert "仅 external 事件" in text
    assert "（无）" in text


@pytest.mark.asyncio
async def test_lookup_qq_audit_only_blacklist_rejected(stores):
    _, requests, audit = stores
    req_id = "req-only-blacklist"
    await audit.append(
        {
            "type": "blacklist_rejected",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "reason": "黑名单拒绝",
            "time": "2026-07-28T10:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "rejected" in text
    assert "黑名单拒绝" in text
    assert result.records[0].blacklist_hit is True
    assert "黑名单" in text


@pytest.mark.asyncio
async def test_lookup_qq_does_not_mutate_requests_store(stores):
    _, requests, audit = stores
    req_id = "req-immutable"
    original = _req(
        req_id,
        status="processed",
        decision="approve",
        reason="原始原因",
        parsed={"name": "张三", "student_id": "261220001", "major": "计算机类"},
    )
    await requests.upsert(original)
    snapshot = await requests.get_by_id(req_id)
    await audit.append(
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": req_id,
            "reason": "lookup 不应写入",
            "time": "2026-07-28T10:00:00+00:00",
        }
    )

    await lookup_qq_records(requests, audit, "123456789")
    unchanged = await requests.get_by_id(req_id)

    assert snapshot is not None and unchanged is not None
    assert unchanged.status == snapshot.status
    assert unchanged.decision == snapshot.decision
    assert unchanged.reason == snapshot.reason
    assert unchanged.parsed == snapshot.parsed


@pytest.mark.asyncio
async def test_lookup_qq_terminal_state_follows_latest_audit_event(stores):
    _, requests, audit = stores
    req_id = "req-multi-event"
    await requests.upsert(_req(req_id, status="pending"))
    await _append_decision(
        audit,
        req_id,
        decision="manual_review",
        reason="初始弱匹配",
        created_at="2026-07-28T08:00:00+00:00",
    )
    await audit.append(
        {
            "type": "admin_command",
            "command": "approve",
            "affected_request_id": req_id,
            "result": "ok",
            "time": "2026-07-28T09:00:00+00:00",
        }
    )
    await audit.append(
        {
            "type": "external_handled",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "message": "最终 external",
            "time": "2026-07-28T10:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert result.records[0].status == "external"
    assert "external" in text
    assert "最终 external" in text


@pytest.mark.asyncio
async def test_apply_terminal_from_audit_preserves_extra_fields():
    from admin.lookup_qq import _apply_terminal_from_audit

    req = PendingRequest(
        id="req-extra",
        group_id="796836121",
        user_id="123456789",
        comment="test",
        flag="flag-value",
        sub_type="add",
        parsed={"name": "张三"},
        match={"strength": "weak"},
        decision="approve",
        confidence=0.9,
        reason="原原因",
        mode="auto",
        status="processed",
        created_at="2026-07-28T08:00:00+00:00",
        match_strength="weak",
        profile="undergraduate",
        matched_student_key="student-key-1",
        admin_user_id="111",
    )
    events = [
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": "req-extra",
            "time": "2026-07-28T10:00:00+00:00",
        }
    ]

    updated = _apply_terminal_from_audit(req, events)

    assert updated.matched_student_key == "student-key-1"
    assert updated.admin_user_id == "111"
    assert updated.profile == "undergraduate"
    assert updated.parsed == {"name": "张三"}
    assert updated.decision == "reject"


@pytest.mark.asyncio
async def test_lookup_qq_sorts_by_late_terminal_event(stores):
    _, requests, audit = stores
    req_id = "req-late-reject"
    await requests.upsert(
        _req(
            req_id,
            created_at="2026-07-01T08:00:00+00:00",
            status="processed",
            decision="approve",
        )
    )
    await audit.append(
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": req_id,
            "reason": "后续拒绝",
            "time": "2026-07-20T10:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.records[0].created_at.startswith("2026-07-01")
    assert result.records[0].last_event_at.startswith("2026-07-20")
    text = format_lookup_qq_result(result)
    assert "最后处理：" in text


@pytest.mark.asyncio
async def test_lookup_qq_sorts_late_processed_before_recent_pending(stores):
    _, requests, audit = stores
    await requests.upsert(
        _req(
            "req-recent-pending",
            created_at="2026-07-20T08:00:00+00:00",
            status="pending",
        )
    )
    await requests.upsert(
        _req(
            "req-old-processed",
            created_at="2026-07-01T08:00:00+00:00",
            status="processed",
            decision="approve",
        )
    )
    await audit.append(
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": "req-old-processed",
            "reason": "7月25拒绝",
            "time": "2026-07-25T10:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    assert [item.request_id for item in result.records] == [
        "req-old-processed",
        "req-recent-pending",
    ]


@pytest.mark.asyncio
async def test_lookup_qq_last_event_at_equals_created_at_without_audit(stores):
    _, requests, audit = stores
    await requests.upsert(
        _req("req-no-audit", created_at="2026-07-15T08:00:00+00:00")
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    record = result.records[0]
    assert record.last_event_at == record.created_at


@pytest.mark.asyncio
async def test_lookup_qq_last_event_at_falls_back_when_audit_has_no_time(stores):
    from admin.lookup_qq import _resolve_last_event_at

    req = _req("req-no-audit-time", created_at="2026-07-10T08:00:00+00:00")
    events = [
        {
            "type": "pending_reparsed",
            "request_id": req.id,
            "user_id": "123456789",
            "reason": "无时间字段",
        }
    ]

    assert _resolve_last_event_at(req, events) == req.created_at


@pytest.mark.asyncio
async def test_lookup_qq_invalid_audit_time_does_not_break_lookup(stores):
    _, requests, audit = stores
    req_id = "req-bad-time"
    await requests.upsert(
        _req(req_id, created_at="2026-07-12T08:00:00+00:00")
    )
    await audit.append(
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": req_id,
            "time": "not-a-valid-time",
        }
    )
    await audit.append(
        {
            "type": "admin_command",
            "command": "approve",
            "affected_request_id": req_id,
            "time": "2026-07-18T09:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 1
    assert result.records[0].last_event_at.startswith("2026-07-18")


def test_parse_audit_datetime_supports_z_and_offset():
    from admin.lookup_qq import _parse_audit_datetime

    z_time = _parse_audit_datetime("2026-07-28T10:00:00Z")
    offset_time = _parse_audit_datetime("2026-07-28T10:00:00+00:00")
    assert z_time == offset_time


def test_latest_audit_time_sorts_z_before_earlier_offset():
    from admin.lookup_qq import _latest_audit_time

    events = [
        {
            "type": "admin_command",
            "command": "approve",
            "affected_request_id": "req-a",
            "time": "2026-07-28T09:00:00+00:00",
        },
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": "req-a",
            "time": "2026-07-28T10:00:00Z",
        },
    ]
    assert _latest_audit_time(events, terminal_only=True) == "2026-07-28T10:00:00Z"


def test_latest_audit_time_sorts_timezone_equivalent_correctly():
    from admin.lookup_qq import _latest_audit_time, _parse_audit_datetime

    events = [
        {
            "type": "admin_command",
            "command": "approve",
            "affected_request_id": "req-a",
            "time": "2026-07-28T09:00:00+00:00",
        },
        {
            "type": "admin_command",
            "command": "reject",
            "affected_request_id": "req-a",
            "time": "2026-07-28T18:00:00+08:00",
        },
    ]
    latest = _latest_audit_time(events, terminal_only=True)
    assert latest == "2026-07-28T18:00:00+08:00"
    assert _parse_audit_datetime(latest) == _parse_audit_datetime("2026-07-28T10:00:00+00:00")


@pytest.mark.asyncio
async def test_lookup_qq_audit_only_restores_parsed_fields(stores):
    _, requests, audit = stores
    req_id = "req-audit-parsed"
    await audit.append(
        {
            "type": "decision_made",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "parsed": {
                "name": "张三",
                "student_id": "261220001",
                "major": "计算机类",
            },
            "decision": "manual_review",
            "reason": "弱匹配",
            "time": "2026-07-28T08:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "张三" in text
    assert "261220001" in text
    assert "计算机类" in text


@pytest.mark.asyncio
async def test_lookup_qq_audit_only_parsed_recovery_is_safe(stores):
    settings, requests, audit = stores
    req_id = "req-audit-parsed-safe"
    await audit.append(
        {
            "type": "decision_made",
            "request_id": req_id,
            "group_id": "796836121",
            "user_id": "123456789",
            "parsed": {
                "name": "李四",
                "student_id": "261220002",
                "major": "软件工程",
                "flag": "must-not-show",
                "raw_event": {"token": "secret-token"},
            },
            "flag": "outer-flag",
            "decision": "manual_review",
            "time": "2026-07-28T08:00:00+00:00",
        }
    )

    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result, settings=settings)
    lowered = text.lower()
    assert "李四" in text
    assert "must-not-show" not in text
    assert "outer-flag" not in text
    assert "raw_event" not in lowered
    assert "secret-token" not in text
