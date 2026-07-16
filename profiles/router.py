from __future__ import annotations

import logging
from typing import Literal

from config import PluginSettings

logger = logging.getLogger(__name__)

AuditProfile = Literal["undergraduate", "graduate"]


def overlapping_group_ids(settings: PluginSettings) -> frozenset[str]:
    return frozenset(settings.target_group_ids & settings.grad_target_group_ids)


def resolve_profile(group_id: str, settings: PluginSettings) -> AuditProfile | None:
    """Map a QQ group to undergraduate / graduate audit profile.

    Returns None when:
    - group is in both undergrad and grad targets (overlap — refuse to process)
    - group is not a configured target
    - graduate group but grad_enabled is false
    """
    gid = str(group_id or "").strip()
    if not gid:
        return None

    in_under = gid in settings.target_group_ids
    in_grad = gid in settings.grad_target_group_ids

    if in_under and in_grad:
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
