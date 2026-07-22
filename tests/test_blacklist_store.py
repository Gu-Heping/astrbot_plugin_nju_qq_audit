"""Blacklist store unit tests (QQ-only)."""

from __future__ import annotations

import json

import pytest

from admin.blacklist import parse_blacklist_add_args
from storage.blacklist_store import (
    UNSUPPORTED_KIND_HINT,
    BlacklistStore,
    normalize_kind,
)


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
async def test_qq_alias_and_user_alias(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    a = await store.add(kind="user", value="111222333", reason="广告号")
    b = await store.add(kind="qq号", value="444555666", reason="异常账号")
    assert a.kind == "user_id"
    assert b.kind == "user_id"
    assert store.match_user_id("111222333") is not None
    assert store.match_user_id("444555666") is not None


@pytest.mark.asyncio
async def test_user_id_group_and_profile_scope(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    await store.add(
        kind="user_id",
        value="11111",
        reason="仅本科群",
        group_id="100",
        profile="undergraduate",
    )
    hit = store.match_request(
        group_id="100",
        user_id="11111",
        profile="undergraduate",
        parsed={},
        match={},
    )
    assert hit is not None
    assert (
        store.match_request(
            group_id="200",
            user_id="11111",
            profile="undergraduate",
            parsed={},
            match={},
        )
        is None
    )
    assert (
        store.match_request(
            group_id="100",
            user_id="11111",
            profile="graduate",
            parsed={},
            match={},
        )
        is None
    )


@pytest.mark.asyncio
async def test_disabled_entry_not_hit(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    entry = await store.add(kind="user_id", value="99999", reason="临时")
    await store.disable(entry.id)
    assert store.match_user_id("99999") is None


@pytest.mark.asyncio
async def test_add_student_exam_notice_grad_rejected(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    for kind in ("student", "exam", "notice", "grad"):
        with pytest.raises(ValueError, match="只支持 QQ"):
            await store.add(kind=kind, value="261220001", reason="误用")


def test_parse_rejects_student_exam_notice_grad():
    for kind in ("student", "exam", "notice", "grad"):
        parsed = parse_blacklist_add_args(kind, "261220001", "confirm", "误用")
        assert parsed == {"mode": "error", "message": UNSUPPORTED_KIND_HINT}


@pytest.mark.asyncio
async def test_match_ignores_parsed_identity_fields(tmp_path):
    store = BlacklistStore(tmp_path / "blacklist.json")
    # 写入历史 student_id 条目到磁盘，模拟旧数据
    path = tmp_path / "blacklist.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "BL-legacy": {
                        "id": "BL-legacy",
                        "kind": "student_id",
                        "value": "261220001",
                        "reason": "旧数据",
                        "enabled": True,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = BlacklistStore(path)
    assert (
        store.match_request(
            group_id="1",
            user_id="9",
            profile="undergraduate",
            parsed={
                "student_id": "261220001",
                "exam_no": "26110100123456",
                "notice_no": "20260001",
            },
            match={"matched_student_key": "张三:生物学:博士"},
        )
        is None
    )


@pytest.mark.asyncio
async def test_legacy_student_kind_listed_but_not_matched(tmp_path):
    path = tmp_path / "blacklist.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "BL-old": {
                        "id": "BL-old",
                        "kind": "student_id",
                        "value": "261220001",
                        "reason": "旧学号条目",
                        "enabled": True,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = BlacklistStore(path)
    listed = await store.list()
    assert len(listed) == 1
    assert listed[0].kind == "student_id"
    assert store.match_user_id("261220001") is None


def test_name_only_not_supported():
    assert normalize_kind("name") is None
