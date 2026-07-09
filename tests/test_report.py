import pytest

from admin.report import build_report_data, classify_manual_reason, format_report, format_unknown
from config import load_settings
from data_source.student_cache import SyncState
from data_source.students import PendingRequest
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
