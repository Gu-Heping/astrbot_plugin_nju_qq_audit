from __future__ import annotations

import json
import re
from typing import Any

from core.ai_parser.models import AiParsedFields

ALLOWED_PROFILES = frozenset({"undergraduate", "graduate"})
ALLOWED_ADMISSION_TYPES = frozenset({"硕士", "博士"})
KNOWN_FIELD_KEYS = frozenset(
    {
        "profile",
        "name",
        "student_id",
        "notice_no",
        "major",
        "academy",
        "admission_type",
        "confidence",
        "ambiguous",
        "warnings",
        "evidence",
    }
)

_JSON_OBJECT_PATTERN = re.compile(r"\{[\s\S]*\}")


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output; raise ValueError on failure."""
    if not text or not str(text).strip():
        raise ValueError("empty response")
    raw = str(text).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_PATTERN.search(raw)
        if not match:
            raise ValueError("non-json response") from None
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("json root must be object")
    return data


def parse_ai_fields_dict(data: dict[str, Any], *, default_profile: str) -> AiParsedFields:
    """Build AiParsedFields from a dict; unknown keys ignored."""
    profile = str(data.get("profile") or default_profile).strip() or default_profile
    if profile not in ALLOWED_PROFILES:
        profile = default_profile

    confidence_raw = data.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    warnings_raw = data.get("warnings") or []
    warnings: list[str] = []
    if isinstance(warnings_raw, list):
        warnings = [str(item) for item in warnings_raw if item is not None]

    evidence_raw = data.get("evidence") or {}
    evidence: dict[str, str | None] = {}
    if isinstance(evidence_raw, dict):
        for key, value in evidence_raw.items():
            if value is None:
                evidence[str(key)] = None
            else:
                evidence[str(key)] = str(value)

    def _opt_str(key: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return AiParsedFields(
        profile=profile,
        name=_opt_str("name"),
        student_id=_opt_str("student_id"),
        notice_no=_opt_str("notice_no"),
        major=_opt_str("major"),
        academy=_opt_str("academy"),
        admission_type=_opt_str("admission_type"),
        confidence=confidence,
        ambiguous=bool(data.get("ambiguous", False)),
        warnings=warnings,
        evidence=evidence,
    )


def ai_fields_json_schema_hint() -> str:
    return (
        '{"profile":"undergraduate|graduate","name":null,"student_id":null,'
        '"notice_no":null,"major":null,"academy":null,"admission_type":null,'
        '"confidence":0.0,"ambiguous":false,"warnings":[],'
        '"evidence":{"name":null,"student_id":null,"notice_no":null,'
        '"major":null,"academy":null,"admission_type":null}}'
    )
