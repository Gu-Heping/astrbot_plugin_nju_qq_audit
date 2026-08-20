import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.report import (
    build_grad_review_data,
    build_report_data,
    classify_manual_reason,
    format_grad_review_detail,
    format_grad_review_report,
    format_report,
    format_unknown,
)
from config import load_settings
from data_source.student_cache import SyncState
from data_source.students import ActionResult, PendingRequest
from graduate.cache import GraduateStudentCache
from graduate.models import GraduateStudent
from storage.requests_store import RequestsStore, new_request_id


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _req(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id="796836121",
        user_id="111",
        comment="张三 电子",
        flag="flag",
        sub_type="add",
        parsed={"name": "张三", "major": "电子"},
        match={"strength": "weak"},
        decision="manual_review",
        confidence=0.4,
        reason="姓名+专业弱匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T12:00:00+00:00",
        match_strength="weak",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _grad_student(**kwargs) -> GraduateStudent:
    defaults = dict(
        source_id="g1",
        admission_type="硕士",
        college="测试学院",
        major_code="085400",
        major_name="电子信息",
        name="张测试",
        key="g1",
    )
    defaults.update(kwargs)
    return GraduateStudent(**defaults)


def _grad_req(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id="200",
        user_id="1234567890",
        comment="问题：姓名 专业 硕or博\n答案：张测试 999999999999 硕",
        flag="grad-flag",
        sub_type="add",
        parsed={"name": "张测试", "admission_type": "硕士", "major_text": "旧专业"},
        match={"strength": "weak"},
        decision="approve",
        confidence=0.4,
        reason="历史管理员通过",
        mode="record-only",
        status="processed",
        processed_at="2026-07-09T12:05:00+00:00",
        action_result=ActionResult(ok=True, message="ok"),
        admin_override=True,
        created_at="2026-07-09T12:00:00+00:00",
        match_strength="weak",
        profile="graduate",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def test_classify_weak_major():
    assert classify_manual_reason(_req()) == "专业弱匹配"


def test_classify_non26():
    req = _req(reason="学号非26级（前两位非26），需人工复核")
    assert classify_manual_reason(req) == "非26级"


@pytest.mark.asyncio
async def test_unknown_report_no_flag(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_req(flag="secret-flag"))
    data = await build_report_data(store, settings, days=7, sample_limit=5)
    text = format_unknown(data)
    assert "secret-flag" not in text
    assert "flag" not in text


@pytest.mark.asyncio
async def test_report_includes_counts(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_req())
    data = await build_report_data(store, settings)
    text = format_report(data, SyncState(), release_running=False)
    assert "待处理" in text
    assert "专业弱匹配" in text or "需人工" in text


@pytest.mark.asyncio
async def test_list_since_skips_invalid_timestamp(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = RequestsStore(tmp_path / "requests.json")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    await store.upsert(_req(created_at="not-a-date"))
    await store.upsert(_req(created_at=recent))
    records = await store.list_since(days=7)
    assert len(records) == 1
    assert records[0].created_at == recent


@pytest.mark.asyncio
async def test_grad_review_report_counts_approved_related(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100",
                "grad_enabled": True,
                "grad_target_group_ids": "200",
            }
        )
    )
    store = RequestsStore(tmp_path / "requests.json")
    grad_cache = GraduateStudentCache(tmp_path)
    grad_cache.save_students(
        [
            _grad_student(),
            _grad_student(
                source_id="g2",
                key="g2",
                name="李测试",
                admission_type="博士",
                major_code="010101",
                major_name="哲学",
            ),
        ]
    )
    await store.upsert(_grad_req(id="REQ-release-only"))
    await store.upsert(
        _grad_req(
            id="REQ-auto",
            user_id="1112223334",
            comment="李测试 010101 博",
            parsed={"name": "李测试", "admission_type": "博士", "major_text": "哲学"},
            status="external",
            processed_at=None,
        )
    )
    await store.upsert(
        _grad_req(
            id="REQ-pending-not-included",
            status="pending",
            decision="manual_review",
        )
    )
    await store.upsert(_req(id="REQ-undergrad", status="processed", decision="approve"))

    data = await build_grad_review_data(store, settings, grad_cache)
    assert data.total_reviewed == 2
    assert data.would_release_now == 2
    assert data.would_auto_now == 1
    assert data.release_only_needs_admin_notice == 1
    assert data.counts["release_only_needs_admin_notice"] == 1
    assert data.counts["would_auto_now"] == 1
    text = format_grad_review_report(data)
    assert "研究生审核历史复盘" in text
    assert "复盘样本：2" in text
    assert "现在可自动通过：1" in text


@pytest.mark.asyncio
async def test_grad_review_blocked_categories(tmp_path):
    settings = load_settings(
        DummyConfig({"grad_enabled": True, "grad_target_group_ids": "200"})
    )
    store = RequestsStore(tmp_path / "requests.json")
    grad_cache = GraduateStudentCache(tmp_path)
    grad_cache.save_students(
        [
            _grad_student(),
            _grad_student(source_id="g2", key="g2", name="重名人", major_name="专业A"),
            _grad_student(source_id="g3", key="g3", name="重名人", major_name="专业B"),
        ]
    )
    await store.upsert(_grad_req(id="REQ-name-major-no-type", comment="张测试 电子信息"))
    await store.upsert(_grad_req(id="REQ-missing-type", comment="张测试"))
    await store.upsert(_grad_req(id="REQ-name-not-found", comment="赵测试 硕"))
    await store.upsert(_grad_req(id="REQ-not-unique", comment="重名人 硕"))

    data = await build_grad_review_data(store, settings, grad_cache)
    assert data.counts["release_only_needs_admin_notice"] == 1
    assert data.counts["missing_admission_type"] == 1
    assert data.counts["name_not_found"] == 1
    assert data.counts["name_type_not_unique"] == 1
    assert data.multi_candidate_total == 1
    assert data.multi_candidate_counts[2] == 1
    text = format_grad_review_report(data)
    assert "多候选分析" in text
    assert "2 人候选：1" in text
    assert "不进入 release" in text


@pytest.mark.asyncio
async def test_grad_review_detail_redacts_sensitive_fields(tmp_path):
    settings = load_settings(
        DummyConfig({"grad_enabled": True, "grad_target_group_ids": "200"})
    )
    store = RequestsStore(tmp_path / "requests.json")
    grad_cache = GraduateStudentCache(tmp_path)
    grad_cache.save_students([_grad_student()])
    await store.upsert(_grad_req())

    data = await build_grad_review_data(store, settings, grad_cache, detail_limit=10)
    text = format_grad_review_detail(data, limit=10)
    assert "1234567890" not in text
    assert "123****890" in text
    assert "999999999999" not in text
    assert "999****999" in text
    assert "答案：张测试" not in text
    assert "答案：某同学" in text
