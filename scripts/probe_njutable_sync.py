"""Local NJUTable sync probe. Usage:
  set NJUTABLE_API_TOKEN=... && python scripts/probe_njutable_sync.py
  python scripts/probe_njutable_sync.py --token ... [--ignore-status]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_settings
from data_source.njutable_client import NjuTableClient, clear_base_token_cache
from data_source.njutable_provider import filter_students_for_sync, map_row_to_student, sync_students
from data_source.student_cache import StudentCache


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Probe NJUTable sync locally")
    parser.add_argument("--token", default=os.environ.get("NJUTABLE_API_TOKEN", ""))
    parser.add_argument("--table", default="考生信息-校对表")
    parser.add_argument("--ignore-status", action="store_true")
    parser.add_argument("--server", default="https://table.nju.edu.cn")
    args = parser.parse_args()

    if not args.token:
        print("ERROR: 需要 --token 或环境变量 NJUTABLE_API_TOKEN")
        return 1

    cfg = DummyConfig(
        {
            "student_source": "nju_table",
            "njutable_api_token": args.token.strip(),
            "njutable_table_name": args.table,
            "njutable_server_url": args.server,
            "njutable_ignore_status_filter": args.ignore_status,
        }
    )
    settings = load_settings(cfg)
    cache = StudentCache(ROOT / "data" / "probe_sync_tmp")
    clear_base_token_cache()

    print("=== NJUTable probe ===")
    print(f"server: {settings.njutable_server_url}")
    print(f"table: {settings.njutable_table_name}")
    print(f"ignore_status_filter: {settings.njutable_ignore_status_filter}")
    print(f"allowed_statuses: {settings.njutable_allowed_statuses}")
    print(f"name_col: {settings.njutable_cols.name}")
    print(f"status_col: {settings.njutable_cols.status}")

    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            client = NjuTableClient(settings)
            base = await client.get_base_token(session)
            print(f"dtable_uuid: {base.get('dtable_uuid', '(none)')}")
            rows = await client.list_all_rows(session)
            raw = len(rows)
            mapped_students = [s for row in rows if (s := map_row_to_student(row, settings))]
            filtered = filter_students_for_sync(mapped_students, settings)
            print(f"raw_rows: {raw}")
            print(f"mapped: {len(mapped_students)}")
            print(f"filtered: {len(filtered)}")

            if rows:
                sample = rows[0]
                keys = sorted(k for k in sample.keys() if not k.startswith("_"))
                print(f"first_row_keys ({len(keys)}): {keys[:20]}")
                print(f"first_row_name_field ({settings.njutable_cols.name!r}): {sample.get(settings.njutable_cols.name)!r}")
                print(f"first_row_status_field ({settings.njutable_cols.status!r}): {sample.get(settings.njutable_cols.status)!r}")

            statuses: dict[str, int] = {}
            for s in mapped_students:
                key = s.status or "(空)"
                statuses[key] = statuses.get(key, 0) + 1
            top = sorted(statuses.items(), key=lambda x: -x[1])[:8]
            print(f"status_distribution: {top}")

            state = await sync_students(settings, cache, session)
            print(f"sync_state: row_count={state.row_count}, filtered_count={state.filtered_count}")
            if filtered:
                s0 = filtered[0]
                print(f"sample_student: name={s0.name!r}, student_id={s0.student_id!r}, status={s0.status!r}")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
