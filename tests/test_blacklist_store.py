"""Blacklist store unit tests."""

from __future__ import annotations

import pytest

from storage.blacklist_store import BlacklistStore


@pytest.mark.asyncio
async def test_add_list_remove_user_id(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    entry = await store.add(
        kind="qq",
        value="123456789",
        reason="家长申请",
        created_by="admin",
    )
    assert entry.kind == "user_id"
    listed = await store.list()
    assert len(listed) == 1
    assert listed[0].id == entry.id
    removed = await store.remove(entry.id)
    assert removed is not None
    assert await store.list() == []


@pytest.mark.asyncio
async def test_user_id_group_and_profile_scope(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    await store.add(
        kind="user_id",
        value="111",
        reason="仅本科群",
        group_id="100",
        profile="undergraduate",
    )
    hit = store.match_request(
        group_id="100",
        user_id="111",
        profile="undergraduate",
        parsed={},
        match={},
    )
    assert hit is not None
    assert (
        store.match_request(
            group_id="200",
            user_id="111",
            profile="undergraduate",
            parsed={},
            match={},
        )
        is None
    )
    assert (
        store.match_request(
            group_id="100",
            user_id="111",
            profile="graduate",
            parsed={},
            match={},
        )
        is None
    )


@pytest.mark.asyncio
async def test_student_exam_notice_hits(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    await store.add(kind="student", value="261220001", reason="异常学号")
    await store.add(kind="exam", value="26110100123456", reason="异常考生号")
    await store.add(kind="notice", value="20260001", reason="异常通知书")
    assert (
        store.match_request(
            group_id="1",
            user_id="9",
            profile="undergraduate",
            parsed={"student_id": "261220001"},
            match={},
        )
        is not None
    )
    assert (
        store.match_request(
            group_id="1",
            user_id="9",
            profile="undergraduate",
            parsed={"exam_no": "26110100123456"},
            match={},
        )
        is not None
    )
    assert (
        store.match_request(
            group_id="1",
            user_id="9",
            profile="undergraduate",
            parsed={"notice_no_candidates": ["20260001"]},
            match={},
        )
        is not None
    )


@pytest.mark.asyncio
async def test_graduate_key_hit(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    await store.add(kind="grad", value="张三:生物学:博士", reason="异常研究生")
    hit = store.match_request(
        group_id="200",
        user_id="1",
        profile="graduate",
        parsed={},
        match={"matched_student_key": "张三:生物学:博士"},
    )
    assert hit is not None


@pytest.mark.asyncio
async def test_disabled_entry_not_hit(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    entry = await store.add(kind="user_id", value="999", reason="临时")
    await store.disable(entry.id)
    assert store.match_user_id("999") is None


def test_name_only_not_supported():
    from storage.blacklist_store import normalize_kind

    assert normalize_kind("name") is None
