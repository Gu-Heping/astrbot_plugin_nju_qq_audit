import asyncio
import json
from datetime import datetime, timedelta, timezone

from storage.list_cache import AdminListCacheStore


def _run(coro):
    return asyncio.run(coro)


def test_refresh_builds_index_map(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    index_map = _run(store.refresh("111", ["REQ-a", "REQ-b"]))
    assert index_map == {1: "REQ-a", 2: "REQ-b"}
    assert store.resolve("111", 1) == "REQ-a"
    assert store.resolve("111", 2) == "REQ-b"


def test_refresh_overwrites_previous(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    _run(store.refresh("111", ["REQ-old"]))
    index_map = _run(store.refresh("111", ["REQ-new"]))
    assert index_map == {1: "REQ-new"}
    assert store.resolve("111", 1) == "REQ-new"
    assert store.resolve("111", 2) is None


def test_admin_isolation(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    _run(store.refresh("111", ["REQ-a"]))
    _run(store.refresh("222", ["REQ-b"]))
    assert store.resolve("111", 1) == "REQ-a"
    assert store.resolve("222", 1) == "REQ-b"


def test_append_increments(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    n1 = _run(store.append("111", "REQ-1"))
    n2 = _run(store.append("111", "REQ-2"))
    assert n1 == 1
    assert n2 == 2
    assert store.resolve("111", 2) == "REQ-2"


def test_append_deduplicates_same_request(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    n1 = _run(store.append("111", "REQ-1"))
    n2 = _run(store.append("111", "REQ-1"))
    assert n1 == 1
    assert n2 == 1


def test_append_respects_max_items(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    for i in range(51):
        _run(store.append("111", f"REQ-{i}"))
    entry = store.load()["admins"]["111"]["items"]
    assert len(entry) == 50
    assert "REQ-0" not in entry.values()
    assert "REQ-50" in entry.values()


def test_expired_cache_lookup_fails(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    data = {
        "version": 1,
        "admins": {
            "111": {
                "updated_at": past,
                "expires_at": past,
                "items": {"1": "REQ-a"},
            }
        },
    }
    store.path.write_text(json.dumps(data), encoding="utf-8")
    assert store.is_expired("111")
    assert store.resolve("111", 1) is None


def test_does_not_store_sensitive_fields(tmp_path):
    store = AdminListCacheStore(tmp_path / "list_cache.json")
    _run(store.refresh("111", ["REQ-a"]))
    raw = store.path.read_text(encoding="utf-8")
    assert "flag" not in raw
    assert "token" not in raw
