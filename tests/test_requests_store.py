import asyncio

from storage.requests_store import RequestsStore, new_request_id
from data_source.students import PendingRequest


def test_requests_empty_restore(tmp_path):
    store = RequestsStore(tmp_path / "requests.json")
    assert store._read_unlocked()["version"] == 3


def test_flag_idempotent(tmp_path):
    store = RequestsStore(tmp_path / "requests.json")
    req = PendingRequest(
        id=new_request_id(),
        group_id="1093442531",
        user_id="123",
        comment="test",
        flag="flag-abc",
        sub_type="add",
        parsed={},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="test",
        mode="record-only",
        status="pending",
        created_at="2026-01-01T00:00:00+00:00",
    )
    asyncio.run(store.upsert(req))
    got = asyncio.run(store.get_by_flag("flag-abc"))
    assert got is not None
    assert got.id == req.id


def test_public_dict_no_flag():
    req = PendingRequest(
        id="REQ-1",
        group_id="1",
        user_id="2",
        comment="c",
        flag="secret-flag",
        sub_type="add",
        parsed={},
        match={},
        decision="manual_review",
        confidence=0.1,
        reason="r",
        mode="record-only",
        status="pending",
        created_at="t",
    )
    public = req.to_public_dict()
    assert "flag" not in public
