from __future__ import annotations

import logging
from typing import Literal

from config import PluginSettings

logger = logging.getLogger(__name__)

AuditProfile = Literal["undergraduate", "graduate"]


def overlapping_group_ids(settings: PluginSettings) -> frozenset[str]:
    """Overlap only matters when graduate audit is enabled."""
    if not settings.grad_enabled:
        return frozenset()
    return frozenset(settings.target_group_ids & settings.grad_target_group_ids)


def configured_audit_group_ids(settings: PluginSettings) -> frozenset[str]:
    """Groups the plugin should process for join/leave/reconcile.

    Excludes overlap groups (neither profile may process them when grad is on).
    """
    under = set(settings.target_group_ids)
    grad: set[str] = set()
    if settings.grad_enabled:
        grad = set(settings.grad_target_group_ids)
        overlap = under & grad
        under -= overlap
        grad -= overlap
    return frozenset(under | grad)


def resolve_profile(group_id: str, settings: PluginSettings) -> AuditProfile | None:
    """Map a QQ group to undergraduate / graduate audit profile.

    Returns None when:
    - group is in both undergrad and grad targets while grad_enabled (overlap)
    - group is not a configured target
    - graduate group but grad_enabled is false
    """
    gid = str(group_id or "").strip()
    if not gid:
        return None

    in_under = gid in settings.target_group_ids
    in_grad = gid in settings.grad_target_group_ids

    # Overlap only blocks when graduate channel is active.
    if settings.grad_enabled and in_under and in_grad:
        logger.warning(
            "[audit] group_id %s overlaps undergraduate and graduate targets; skip",
            gid,
        )
        return None

    if in_under:
        return "undergraduate"

    if in_grad:
        if not settings.grad_enabled:
            logger.debug("[audit] graduate group %s skipped (grad_enabled=false)", gid)
            return None
        return "graduate"

    return None
