"""AI parse lifecycle: one call per answer revision (v0.4.17)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.release import rematch_and_list_releasable
from config import load_settings
from core.parsed_store import attach_parsed_meta, compute_comment_hash
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import PendingRequest, Student
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_ID = "100"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _student(**kwargs) -> Student:
    base = dict(
        key="261880001",
        name="周七七",
        student_id="261880001",
        major="技术科学试验班",
        updated_at="t",
    )
    base.update(kwargs)
    return Student(**base)


def _pipeline(tmp_path: Path, *, students=None, extra_settings=None):
    cfg = {
        "target_group_ids": GROUP_ID,
        "admin_notify": False,
        "student_source": "mock",
        "ai_parse_enabled": True,
        "ai_parse_shadow_mode": False,
        "ai_parse_backend": "openai_compatible",
        "ai_parse_base_url": "http://example.invalid/v1",
        "ai_parse_model": "test",
        "ai_parse_on_rematch": False,
    }
    if extra_settings:
        cfg.update(extra_settings)
    settings = load_settings(DummyConfig(cfg))
    cache = StudentCache(tmp_path)
    if students is not None:
        cache.save_students(students)
    return AuditPipeline(
        settings,
        RequestsStore(tmp_path / "requests.json"),
        AuditLog(tmp_path / "audit.jsonl", settings),
        RuntimeStore(tmp_path / "runtime.json"),
        cache,
        MagicMock(),
        MagicMock(),
    )


@pytest.fixture
def ai_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_ai(settings, *, profile, raw_comment, parsed, incomplete, **kwargs):
        calls.append(
            {
                "profile": profile,
                "raw_comment": raw_comment,
                "incomplete": incomplete,
            }
        )
        # Simulate merge for incomplete undergrad comments with credentials.
        if "261880001" in (raw_comment or "") and not parsed.name:
            parsed.name = "周七七"
            parsed.student_id = "261880001"
            parsed.major = "技术科学试验班"
            if "ai_parse_merged" not in parsed.parse_errors:
                parsed.parse_errors.append("ai_parse_used")
                parsed.parse_errors.append("ai_parse_merged")
        return MagicMock(ok=True)

    monkeypatch.setattr("core.pipeline.maybe_run_ai_parse", fake_ai)
    return calls


@pytest.mark.asyncio
async def test_new_request_can_call_ai(tmp_path, ai_calls):
    pipe = _pipeline(tmp_path, students=[_student()])
    event = GroupJoinRequest(
        group_id=GROUP_ID,
        user_id="1",
        # Incomplete deterministic parse (sid only) → non-strong → AI fallback.
        comment="答案：学号261880001",
        flag="f1",
        sub_type="add",
    )
    await pipe._evaluate_undergraduate_request(event, allow_ai_parse=True)
    assert len(ai_calls) == 1


@pytest.mark.asyncio
async def test_rematch_same_comment_does_not_call_ai(tmp_path, ai_calls):
    comment = "答案：周七七+261880001+技术科学试验班"
    parsed = attach_parsed_meta(
        {
            "name": "周七七",
            "student_id": "261880001",
            "major": "技术科学试验班",
            "parse_errors": ["ai_parse_merged"],
        },
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[_student()])
    req = PendingRequest(
        id="r1",
        group_id=GROUP_ID,
        user_id="1",
        comment=comment,
        flag="f1",
        sub_type="add",
        status="pending",
        decision="manual_review",
        confidence=0,
        reason="x",
        mode="auto",
        created_at="t",
        parsed=parsed,
        match={"strength": "none"},
        match_strength="none",
        profile="undergraduate",
    )
    await pipe.requests.upsert(req)
    summary = await pipe.rematch_active_pending(source="test")
    assert summary.scanned >= 1
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r1")
    assert updated is not None
    assert updated.parsed.get("name") == "周七七"
    assert updated.parsed.get("student_id") == "261880001"
    errors = updated.parsed.get("parse_errors") or []
    assert "ai_parse_merged" not in errors or "ai_parse_used" in errors
    assert any(m in errors for m in ("ai_parse_used", "ai_parse_merged", "ai_parse_shadow"))


@pytest.mark.asyncio
async def test_rematch_upgrades_strong_with_new_roster(tmp_path, ai_calls):
    comment = "周七七 261880001"
    parsed = attach_parsed_meta(
        {"name": "周七七", "student_id": "261880001", "parse_errors": ["ai_parse_merged"]},
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[])  # empty first
    req = PendingRequest(
        id="r2",
        group_id=GROUP_ID,
        user_id="2",
        comment=comment,
        flag="f2",
        sub_type="add",
        status="pending",
        decision="manual_review",
        confidence=0,
        reason="none",
        mode="auto",
        created_at="t",
        parsed=parsed,
        match={"strength": "none"},
        match_strength="none",
        profile="undergraduate",
    )
    await pipe.requests.upsert(req)
    # Update roster then rematch
    pipe.cache.save_students([_student()])
    summary = await pipe.rematch_active_pending(source="catchup")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r2")
    assert updated is not None
    assert updated.match_strength == "strong"
    assert summary.upgraded_to_strong >= 1


@pytest.mark.asyncio
async def test_comment_change_allows_ai_again(tmp_path, ai_calls):
    pipe = _pipeline(tmp_path, students=[_student()])
    old_comment = "答案：test"
    old_parsed = attach_parsed_meta(
        {"name": None, "parse_errors": ["unable to parse any field"]},
        comment=old_comment,
        profile="undergraduate",
    )
    existing = PendingRequest(
        id="r3",
        group_id=GROUP_ID,
        user_id="3",
        comment=old_comment,
        flag="f3",
        sub_type="add",
        status="pending",
        decision="manual_review",
        confidence=0,
        reason="x",
        mode="auto",
        created_at="t",
        parsed=old_parsed,
        match={},
        profile="undergraduate",
    )
    await pipe.requests.upsert(existing)
    event = GroupJoinRequest(
        group_id=GROUP_ID,
        user_id="3",
        # Incomplete parse path so AI is still invoked (strong deterministic skips AI).
        comment="答案：学号261880001",
        flag="f3",
        sub_type="add",
    )
    await pipe._audit_and_update_pending(event, existing)
    assert len(ai_calls) == 1
    updated = await pipe.requests.get_by_id("r3")
    assert updated is not None
    assert updated.parsed.get("_comment_hash") == compute_comment_hash(event.comment)
    assert updated.parsed.get("name") == "周七七" or updated.parsed.get("student_id")


@pytest.mark.asyncio
async def test_legacy_pending_without_hash_preserves_ai_fields(tmp_path, ai_calls):
    """Pre-hash stored parse (AI-only name) must be reused, not wiped by rematch."""
    pipe = _pipeline(tmp_path, students=[_student()])
    req = PendingRequest(
        id="r4",
        group_id=GROUP_ID,
        user_id="4",
        comment="无法本地解析的乱码答案",
        flag="f4",
        sub_type="add",
        status="pending",
        decision="manual_review",
        confidence=0,
        reason="x",
        mode="auto",
        created_at="t",
        parsed={
            "name": "周七七",
            "student_id": "261880001",
            "parse_errors": ["ai_parse_merged"],
        },
        match={},
        profile="undergraduate",
    )
    await pipe.requests.upsert(req)
    await pipe.rematch_active_pending(source="test")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r4")
    assert updated is not None
    assert updated.parsed.get("name") == "周七七"
    assert updated.parsed.get("student_id") == "261880001"
    assert updated.match_strength == "strong"


@pytest.mark.asyncio
async def test_ai_attempted_no_fields_rematch_does_not_recall_ai(tmp_path, ai_calls):
    comment = "答案：无法解析"
    parsed = attach_parsed_meta(
        {"parse_errors": ["unable to parse any field", "ai_parse_used"]},
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(
        tmp_path,
        students=[_student()],
        extra_settings={"ai_parse_on_rematch": True},
    )
    await pipe.requests.upsert(
        PendingRequest(
            id="r4e",
            group_id=GROUP_ID,
            user_id="4",
            comment=comment,
            flag="f4e",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed=parsed,
            match={},
            profile="undergraduate",
        )
    )
    await pipe.rematch_active_pending(source="catchup")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r4e")
    assert updated is not None
    assert "ai_parse_used" in (updated.parsed.get("parse_errors") or [])
    # Second rematch must still skip AI because markers were preserved.
    await pipe.rematch_active_pending(source="catchup")
    assert len(ai_calls) == 0


@pytest.mark.asyncio
async def test_legacy_unhashed_ai_markers_survive_rematch(tmp_path, ai_calls):
    """Pre-hash rows with ai_parse_used must keep markers across rematch."""
    pipe = _pipeline(
        tmp_path,
        students=[_student()],
        extra_settings={"ai_parse_on_rematch": True},
    )
    await pipe.requests.upsert(
        PendingRequest(
            id="r4g",
            group_id=GROUP_ID,
            user_id="4",
            comment="答案：无法解析",
            flag="f4g",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed={"parse_errors": ["unable to parse any field", "ai_parse_used"]},
            match={},
            profile="undergraduate",
        )
    )
    await pipe.rematch_active_pending(source="catchup")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r4g")
    assert "ai_parse_used" in (updated.parsed.get("parse_errors") or [])
    await pipe.rematch_active_pending(source="catchup")
    assert len(ai_calls) == 0


@pytest.mark.asyncio
async def test_failed_retry_same_comment_reuses_parsed(tmp_path, ai_calls):
    comment = "答案：周七七+261880001+技术科学试验班"
    parsed = attach_parsed_meta(
        {
            "name": "周七七",
            "student_id": "261880001",
            "major": "技术科学试验班",
            "parse_errors": ["ai_parse_merged"],
        },
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[_student()])
    existing = PendingRequest(
        id="r4f",
        group_id=GROUP_ID,
        user_id="4",
        comment=comment,
        flag="f4f",
        sub_type="add",
        status="failed",
        decision="approve",
        confidence=0,
        reason="x",
        mode="auto",
        created_at="t",
        parsed=parsed,
        match={"strength": "strong"},
        match_strength="strong",
        profile="undergraduate",
        processed_at="t",
    )
    await pipe.requests.upsert(existing)
    event = GroupJoinRequest(
        group_id=GROUP_ID,
        user_id="4",
        comment=comment,
        flag="f4f",
        sub_type="add",
    )
    await pipe._audit_and_act(
        event,
        resubmit=True,
        request_id=existing.id,
        reuse_parsed_from=existing,
    )
    assert len(ai_calls) == 0


@pytest.mark.asyncio
async def test_ai_parse_on_rematch_hashed_unable_to_parse(tmp_path, ai_calls):
    comment = "答案：test"
    parsed = attach_parsed_meta(
        {"parse_errors": ["unable to parse any field"]},
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(
        tmp_path,
        students=[_student()],
        extra_settings={"ai_parse_on_rematch": True},
    )
    await pipe.requests.upsert(
        PendingRequest(
            id="r4b",
            group_id=GROUP_ID,
            user_id="4",
            comment=comment,
            flag="f4b",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed=parsed,
            match={},
            profile="undergraduate",
        )
    )
    await pipe.rematch_active_pending(source="test")
    assert len(ai_calls) == 1


@pytest.mark.asyncio
async def test_ai_merged_with_stale_unable_to_parse_is_reused(tmp_path, ai_calls):
    comment = "答案：乱码无法本地解析"
    parsed = attach_parsed_meta(
        {
            "name": "周七七",
            "student_id": "261880001",
            "parse_errors": ["unable to parse any field", "ai_parse_merged"],
        },
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[_student()])
    await pipe.requests.upsert(
        PendingRequest(
            id="r4d",
            group_id=GROUP_ID,
            user_id="4",
            comment=comment,
            flag="f4d",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed=parsed,
            match={},
            profile="undergraduate",
        )
    )
    await pipe.rematch_active_pending(source="test")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r4d")
    assert updated.parsed.get("name") == "周七七"
    assert updated.match_strength == "strong"


@pytest.mark.asyncio
async def test_whitespace_only_comment_change_reuses_parsed(tmp_path, ai_calls):
    old_comment = "答案：周七七+261880001"
    new_comment = "答案： 周七七 + 261880001"
    assert compute_comment_hash(old_comment) == compute_comment_hash(new_comment)
    parsed = attach_parsed_meta(
        {
            "name": "周七七",
            "student_id": "261880001",
            "parse_errors": ["ai_parse_merged"],
        },
        comment=old_comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[_student()])
    existing = PendingRequest(
        id="r4c",
        group_id=GROUP_ID,
        user_id="4",
        comment=old_comment,
        flag="f4c",
        sub_type="add",
        status="pending",
        decision="manual_review",
        confidence=0,
        reason="x",
        mode="auto",
        created_at="t",
        parsed=parsed,
        match={},
        profile="undergraduate",
    )
    await pipe.requests.upsert(existing)
    event = GroupJoinRequest(
        group_id=GROUP_ID,
        user_id="4",
        comment=new_comment,
        flag="f4c",
        sub_type="add",
    )
    await pipe._audit_and_update_pending(event, existing)
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r4c")
    assert updated.parsed.get("name") == "周七七"
    errors = updated.parsed.get("parse_errors") or []
    assert any(m in errors for m in ("ai_parse_used", "ai_parse_merged", "ai_parse_shadow"))


@pytest.mark.asyncio
async def test_ai_parse_on_rematch_only_when_missing(tmp_path, ai_calls):
    pipe = _pipeline(
        tmp_path,
        students=[_student()],
        extra_settings={"ai_parse_on_rematch": True},
    )
    req = PendingRequest(
        id="r5",
        group_id=GROUP_ID,
        user_id="5",
        # Missing stored parse + incomplete comment → rematch may call AI.
        comment="答案：学号261880001",
        flag="f5",
        sub_type="add",
        status="pending",
        decision="manual_review",
        confidence=0,
        reason="x",
        mode="auto",
        created_at="t",
        parsed={},  # missing
        match={},
        profile="undergraduate",
    )
    await pipe.requests.upsert(req)
    await pipe.rematch_active_pending(source="test")
    assert len(ai_calls) == 1


@pytest.mark.asyncio
async def test_release_preview_does_not_call_ai(tmp_path, ai_calls):
    comment = "周七七 261880001"
    parsed = attach_parsed_meta(
        {"name": "周七七", "student_id": "261880001"},
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[_student()])
    await pipe.requests.upsert(
        PendingRequest(
            id="r6",
            group_id=GROUP_ID,
            user_id="6",
            comment=comment,
            flag="f6",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed=parsed,
            match={"strength": "none"},
            match_strength="none",
            profile="undergraduate",
        )
    )
    await rematch_and_list_releasable(
        pipe, pipe.requests, pipe.settings, source="release_preview"
    )
    assert len(ai_calls) == 0


@pytest.mark.asyncio
async def test_catchup_paths_do_not_call_ai(tmp_path, ai_calls):
    comment = "周七七 261880001"
    parsed = attach_parsed_meta(
        {"name": "周七七", "student_id": "261880001", "parse_errors": ["ai_parse_merged"]},
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[_student()])
    await pipe.requests.upsert(
        PendingRequest(
            id="r7",
            group_id=GROUP_ID,
            user_id="7",
            comment=comment,
            flag="f7",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed=parsed,
            match={"strength": "none"},
            match_strength="none",
            profile="undergraduate",
        )
    )
    await rematch_and_list_releasable(
        pipe, pipe.requests, pipe.settings, source="catchup_preview"
    )
    await rematch_and_list_releasable(
        pipe, pipe.requests, pipe.settings, source="catchup_confirm"
    )
    await pipe.rematch_active_pending(source="catchup_batch")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r7")
    errors = updated.parsed.get("parse_errors") or []
    assert any(m in errors for m in ("ai_parse_used", "ai_parse_merged", "ai_parse_shadow"))


@pytest.mark.asyncio
async def test_legacy_empty_parsed_falls_back_to_deterministic(tmp_path, ai_calls):
    pipe = _pipeline(tmp_path, students=[_student()])
    await pipe.requests.upsert(
        PendingRequest(
            id="r8",
            group_id=GROUP_ID,
            user_id="8",
            comment="周七七 261880001",
            flag="f8",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed=None,
            match={},
            match_strength="none",
            profile="undergraduate",
        )
    )
    await pipe.rematch_active_pending(source="test")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r8")
    assert updated is not None
    assert updated.parsed.get("name") == "周七七"
    assert updated.parsed.get("student_id") == "261880001"
    assert updated.match_strength == "strong"


@pytest.mark.asyncio
async def test_partial_stored_rematch_upgrades_via_deterministic(tmp_path, ai_calls):
    """Stored name-only must still reparse comment credentials on rematch."""
    comment = "周七七 261880001"
    parsed = attach_parsed_meta(
        {"name": "周七七", "parse_errors": ["ai_parse_merged"]},
        comment=comment,
        profile="undergraduate",
    )
    pipe = _pipeline(tmp_path, students=[_student()])
    await pipe.requests.upsert(
        PendingRequest(
            id="r9",
            group_id=GROUP_ID,
            user_id="9",
            comment=comment,
            flag="f9",
            sub_type="add",
            status="pending",
            decision="manual_review",
            confidence=0,
            reason="x",
            mode="auto",
            created_at="t",
            parsed=parsed,
            match={"strength": "none"},
            match_strength="none",
            profile="undergraduate",
        )
    )
    await pipe.rematch_active_pending(source="catchup")
    assert len(ai_calls) == 0
    updated = await pipe.requests.get_by_id("r9")
    assert updated.parsed.get("student_id") == "261880001"
    assert updated.match_strength == "strong"
    # Deterministic extracted credentials → do not keep ai_parse_merged (release-safe),
    # but keep an attempt marker so rematch won't call AI again.
    assert "ai_parse_merged" not in (updated.parsed.get("parse_errors") or [])
    assert "ai_parse_used" in (updated.parsed.get("parse_errors") or [])
    assert updated.decision == "approve"


@pytest.mark.asyncio
async def test_legacy_retry_same_comment_skips_ai_without_hash(tmp_path, ai_calls):
    comment = "答案：乱码"
    pipe = _pipeline(tmp_path, students=[_student()])
    existing = PendingRequest(
        id="r10",
        group_id=GROUP_ID,
        user_id="10",
        comment=comment,
        flag="f10",
        sub_type="add",
        status="failed",
        decision="manual_review",
        confidence=0,
        reason="x",
        mode="auto",
        created_at="t",
        parsed={"parse_errors": ["ai_parse_used"]},  # no hash / raw
        match={},
        profile="undergraduate",
        processed_at="t",
    )
    await pipe.requests.upsert(existing)
    await pipe._audit_and_act(
        GroupJoinRequest(
            group_id=GROUP_ID,
            user_id="10",
            comment=comment,
            flag="f10",
            sub_type="add",
        ),
        resubmit=True,
        request_id=existing.id,
        reuse_parsed_from=existing,
    )
    assert len(ai_calls) == 0


def test_comment_hash_stable():
    assert compute_comment_hash("a  b") == compute_comment_hash("a b")
    assert compute_comment_hash("a") != compute_comment_hash("b")
    assert compute_comment_hash("周七七+261880001") == compute_comment_hash(
        "周七七 + 261880001"
    )
    assert compute_comment_hash("张三；261") == compute_comment_hash("张三 ； 261")
