from __future__ import annotations

import subprocess
from pathlib import Path

PLUGIN_VERSION = "v0.3.8"
RECONCILE_LOGIC_VERSION = "v2-invite-matches-pending"
DUPLICATE_POLICY_VERSION = "v2-terminal-never-reapply"


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
