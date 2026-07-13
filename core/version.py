from __future__ import annotations

import subprocess
from pathlib import Path

PLUGIN_VERSION = "v0.3.16"
RECONCILE_LOGIC_VERSION = "v2-invite-matches-pending"
DUPLICATE_POLICY_VERSION = "v5-terminal-never-reapply"
PENDING_UPDATE_POLICY_VERSION = "v1-update-pending-on-comment-change"

# 同 flag 重复 group_request：终态一律忽略，不 release_flag、不复活 pending
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
