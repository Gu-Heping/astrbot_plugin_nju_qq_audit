from __future__ import annotations

import subprocess
from pathlib import Path

PLUGIN_VERSION = "v0.3.20"
RECONCILE_LOGIC_VERSION = "v2-invite-matches-pending"
DUPLICATE_POLICY_VERSION = "v7-terminal-reapply-fingerprint"
PENDING_UPDATE_POLICY_VERSION = "v1-update-pending-on-comment-change"

# 同 flag 永久忽略：stale、ignored
# processed(approve/reject)、external 允许新 attempt（事件指纹 + 时间/防抖判定）
PERMANENT_IGNORE_STATUSES = frozenset({"ignored", "stale"})


def is_reapply_eligible_terminal(req) -> bool:
    if req.status == "external":
        return True
    if req.status == "processed" and req.decision in {"reject", "approve"}:
        return True
    return False


def is_processed_reject(req) -> bool:
    return req.status == "processed" and req.decision == "reject"


def is_permanent_terminal(req) -> bool:
    return req.status in PERMANENT_IGNORE_STATUSES


# 兼容旧 import
TERMINAL_DUPLICATE_STATUSES = frozenset({"processed", "external", "ignored", "stale"})


def get_git_commit() -> str | None:
    root = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            commit = (result.stdout or "").strip()
            return commit or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None
