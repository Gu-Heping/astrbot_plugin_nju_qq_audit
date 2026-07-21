from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AiParsedFields:
    profile: str
    name: str | None = None
    student_id: str | None = None
    exam_no: str | None = None
    notice_no: str | None = None
    major: str | None = None
    academy: str | None = None
    admission_type: str | None = None
    confidence: float = 0.0
    ambiguous: bool = False
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, str | None] = field(default_factory=dict)

    def to_log_dict(self) -> dict[str, Any]:
        """Safe summary for logs (field keys only, no raw evidence dump)."""
        present = [
            key
            for key in (
                "name",
                "student_id",
                "exam_no",
                "notice_no",
                "major",
                "academy",
                "admission_type",
            )
            if getattr(self, key, None)
        ]
        return {
            "profile": self.profile,
            "fields": present,
            "confidence": self.confidence,
            "ambiguous": self.ambiguous,
            "warnings_count": len(self.warnings),
        }


@dataclass
class AiParseResult:
    ok: bool
    fields: AiParsedFields | None = None
    error: str | None = None
    raw_response_hash: str | None = None
    model: str | None = None
