"""Tests for graduate strong-match releasable predicates."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.grad_release import is_grad_releasable, list_grad_releasable
from admin.release import is_releasable
from config import load_settings
from data_source.students import PendingRequest
from storage.requests_store import RequestsStore, new_request_id

GRAD_GROUP = "200"
UNDER_GROUP = "796836121"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**kwargs):
    base = {
        "target_group_ids": UNDER_GROUP,
        "grad_enabled": True,
        "grad_target_group_ids": GRAD_GROUP,
        "ai_parse_allow_auto_approve": False,
    }
    base.update(kwargs)
    return load_settings(DummyConfig(base))


def _grad_strong(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id=GRAD_GROUP,
        user_id="111",
        comment="张三 生物学 博士",
        flag="flag-grad-1",
        sub_type="add",
        profile="graduate",
        parsed={
            "name": "张三",
            "admission_type": "博士",
            "major_text": "生物学",
        },
        match={
            "strength": "strong",
            "candidate_count": 1,
            "matched_student_key": "张三:生物学:博士",
            "major_name": "生物学",
        },
        decision="approve",
        confidence=0.95,
        reason="研究生强匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-20T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _under_strong(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id=UNDER_GROUP,
        user_id="222",
        comment="李四 261220001",
        flag="flag-under-1",
        sub_type="add",
        profile="undergraduate",
        parsed={"name": "李四", "student_id": "261220001"},
        match={"strength": "strong"},
        decision="approve",
        confidence=0.95,
        reason="强匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-20T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def test_is_grad_releasable_strong_approve():
    settings = _settings()
    assert is_grad_releasable(_grad_strong(), settings)
    assert not is_releasable(_grad_strong(), settings)


def test_undergrad_strong_not_grad_releasable():
    settings = _settings()
    req = _under_strong()
    assert is_releasable(req, settings)
    assert not is_grad_releasable(req, settings)


def test_grad_weak_manual_not_releasable():
    settings = _settings()
    assert not is_grad_releasable(
        _grad_strong(decision="manual_review", match_strength="weak"),
        settings,
    )


def test_candidate_count_not_one():
    settings = _settings()
    assert not is_grad_releasable(
        _grad_strong(match={"strength": "strong", "candidate_count": 2}),
        settings,
    )
    assert not is_grad_releasable(
        _grad_strong(match={"strength": "strong"}),
        settings,
    )


def test_missing_admission_type():
    settings = _settings()
    assert not is_grad_releasable(
        _grad_strong(parsed={"name": "张三", "major_text": "生物学"}),
        settings,
    )


def test_missing_major():
    settings = _settings()
    assert not is_grad_releasable(
        _grad_strong(
            parsed={"name": "张三", "admission_type": "博士"},
            match={"strength": "strong", "candidate_count": 1},
        ),
        settings,
    )


def test_major_from_match_major_name_ok():
    settings = _settings()
    assert is_grad_releasable(
        _grad_strong(
            parsed={"name": "张三", "admission_type": "硕士"},
            match={
                "strength": "strong",
                "candidate_count": 1,
                "major_name": "哲学",
            },
        ),
        settings,
    )


def test_group_not_in_grad_targets():
    settings = _settings()
    assert not is_grad_releasable(_grad_strong(group_id="999"), settings)


def test_empty_flag():
    settings = _settings()
    assert not is_grad_releasable(_grad_strong(flag=""), settings)


def test_ai_parse_merged_blocks_when_auto_approve_disabled():
    settings = _settings(ai_parse_allow_auto_approve=False)
    req = _grad_strong(
        parsed={
            "name": "张三",
            "admission_type": "博士",
            "major_text": "生物学",
            "parse_errors": ["ai_parse_merged"],
        }
    )
    assert not is_grad_releasable(req, settings)


def test_ai_parse_merged_allowed_when_flag_enabled():
    settings = _settings(ai_parse_allow_auto_approve=True)
    req = _grad_strong(
        parsed={
            "name": "张三",
            "admission_type": "博士",
            "major_text": "生物学",
            "parse_errors": ["ai_parse_merged"],
        }
    )
    assert is_grad_releasable(req, settings)


@pytest.mark.asyncio
async def test_list_grad_releasable_sorts_by_created_at(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    later = _grad_strong(
        id="REQ-later",
        created_at="2026-07-20T02:00:00+00:00",
        parsed={
            "name": "后到",
            "admission_type": "硕士",
            "major_text": "化学",
        },
    )
    earlier = _grad_strong(
        id="REQ-earlier",
        created_at="2026-07-20T01:00:00+00:00",
        parsed={
            "name": "先到",
            "admission_type": "博士",
            "major_text": "物理",
        },
    )
    await store.upsert(later)
    await store.upsert(earlier)
    await store.upsert(_under_strong(id="REQ-under"))

    items = await list_grad_releasable(store, settings)
    assert [r.id for r in items] == ["REQ-earlier", "REQ-later"]
