from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReconcileResult:
    handled: bool
    reason: str
    request_id: str | None = None
    message: str | None = None

    @classmethod
    def not_handled(cls, reason: str, message: str | None = None) -> ReconcileResult:
        return cls(handled=False, reason=reason, message=message)

    @classmethod
    def success(cls, request_id: str, message: str) -> ReconcileResult:
        return cls(handled=True, reason="handled", request_id=request_id, message=message)
