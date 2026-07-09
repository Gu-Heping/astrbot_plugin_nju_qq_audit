import asyncio

from storage.runtime_store import RuntimeStore


def test_runtime_only_mode(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.json")
    asyncio.run(store.set_mode("manual", "10001"))
    data = store.load()
    assert data["mode"] == "manual"
    assert "target_group_ids" not in data


def test_runtime_reset(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.json")
    asyncio.run(store.set_mode("auto", "10001"))
    asyncio.run(store.clear_mode())
    assert store.get_mode_override() is None
