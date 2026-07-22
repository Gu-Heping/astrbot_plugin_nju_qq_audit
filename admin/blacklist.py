from __future__ import annotations

from storage.blacklist_store import (
    UNSUPPORTED_KIND_HINT,
    BlacklistEntry,
    BlacklistStore,
    is_unsupported_kind_alias,
    normalize_kind,
)


KIND_LABELS = {
    "user_id": "QQ",
}


def format_blacklist_help() -> str:
    return "\n".join(
        [
            "黑名单管理（按 QQ 号拦截）",
            "",
            "命令：",
            "/audit blacklist list",
            "/audit blacklist add 3 confirm 家长申请",
            "/audit blacklist add qq 123456789 confirm 家长号",
            "/audit blacklist remove BL-xxxx confirm",
            "/audit blacklist check <QQ号或编号>",
            "",
            "说明：",
            "- add 3 会拉黑第 3 条申请的 QQ 号",
            "- 黑名单只按 QQ 号拦截，不按学号/考生号拦截，避免误伤学生本人",
            "- 命中黑名单会阻止 release/catchup 放行",
            "- 申请时命中黑名单会按配置自动拒绝",
            "- 对申请人使用中性拒绝理由",
            "- 黑名单优先级高于 strong 匹配",
        ]
    )


def _display_value(entry: BlacklistEntry) -> str:
    return entry.value


def format_blacklist_list(entries: list[BlacklistEntry]) -> str:
    if not entries:
        return "黑名单为空。"
    lines = [f"黑名单：{len(entries)} 条", ""]
    for entry in entries:
        kind = KIND_LABELS.get(entry.kind, entry.kind)
        scope = []
        if entry.group_id:
            scope.append(f"群 {entry.group_id}")
        if entry.profile:
            scope.append(entry.profile)
        scope_text = f"（{' · '.join(scope)}）" if scope else ""
        lines.extend(
            [
                f"{entry.id} · {kind} {_display_value(entry)}{scope_text}",
                f"原因：{entry.reason}",
                f"创建：{entry.created_at[:16]} by {entry.created_by or '未知'}",
                "",
            ]
        )
    lines.append("删除：/audit blacklist remove <BL-id> confirm")
    return "\n".join(lines)


def format_blacklist_entry(entry: BlacklistEntry, *, title: str = "已加入黑名单") -> str:
    kind = KIND_LABELS.get(entry.kind, entry.kind)
    return "\n".join(
        [
            title,
            f"编号：{entry.id}",
            f"类型：{kind}",
            f"值：{_display_value(entry)}",
            f"原因：{entry.reason}",
        ]
    )


def parse_blacklist_add_args(arg1: str, arg2: str, arg3: str, rest: str = "") -> dict | None:
    """Parse add subcommand pieces.

    Forms:
      add <list_index> confirm <reason>
      add qq|user <QQ号> confirm <reason>
    """
    a1 = (arg1 or "").strip()
    a2 = (arg2 or "").strip()
    a3 = (arg3 or "").strip()
    trailing = (rest or "").strip()
    if not a1:
        return None
    if a1.isdigit() and a2.lower() == "confirm":
        reason = " ".join(x for x in [a3, trailing] if x).strip()
        if not reason:
            return None
        return {"mode": "list_ref", "ref": a1, "reason": reason}
    if is_unsupported_kind_alias(a1):
        return {"mode": "error", "message": UNSUPPORTED_KIND_HINT}
    kind = normalize_kind(a1)
    if kind and a3.lower() == "confirm":
        reason = trailing.strip()
        if not reason:
            return None
        return {"mode": "direct", "kind": kind, "value": a2, "reason": reason}
    return None


async def check_blacklist_query(store: BlacklistStore, query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "请提供 QQ 号或黑名单编号。"
    if q.upper().startswith("BL-"):
        entry = await store.get(q)
        if entry is None:
            return f"未找到黑名单条目：{q}"
        return format_blacklist_entry(entry, title="黑名单条目")
    hit = store.match_user_id(q)
    if hit is None:
        return f"未命中黑名单：{q}"
    entry = await store.get(hit.entry_id)
    if entry is None:
        return f"命中黑名单 {hit.entry_id}（原因：{hit.reason}）"
    return format_blacklist_entry(entry, title="命中黑名单")
