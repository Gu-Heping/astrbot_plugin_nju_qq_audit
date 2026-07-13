from __future__ import annotations

import subprocess
from pathlib import Path

PLUGIN_VERSION = "v0.3.11"
RECONCILE_LOGIC_VERSION = "v2-invite-matches-pending"
DUPLICATE_POLICY_VERSION = "v4-external-reapply-on-request"

# processed 同 flag 一律视为 QQ 重复事件
# external 收到新的 group_request 时允许重新审核（退群重进/同 flag 重申请）
# stale/ignored 在用户已不在群时可重新申请
REAPPLY_CHECK_TERMINAL_STATUSES = frozenset({"stale", "ignored"})


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
