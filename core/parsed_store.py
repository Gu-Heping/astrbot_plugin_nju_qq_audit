"""Store/reuse parsed application fields across rematch without re-calling AI."""

from __future__ import annotations

import hashlib
from typing import Any

from core.normalize import normalize_whitespace
from core.parser import ParsedApplication
from graduate.models import GraduateParsedApplication

PARSER_VERSION = "v0.4.17"


def compute_comment_hash(comment: str) -> str:
    text = normalize_whitespace(comment or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def undergrad_parsed_from_dict(
    data: dict[str, Any] | None, raw_comment: str
) -> ParsedApplication:
    payload = data or {}
    return ParsedApplication(
        raw=str(payload.get("raw") or raw_comment or ""),
        name=payload.get("name"),
        student_id=payload.get("student_id"),
        notice_no=payload.get("notice_no"),
        major=payload.get("major"),
        academy=payload.get("academy"),
        notice_no_candidates=list(payload.get("notice_no_candidates") or []),
        parse_errors=list(payload.get("parse_errors") or []),
    )


def grad_parsed_from_dict(
    data: dict[str, Any] | None, raw_comment: str
) -> GraduateParsedApplication:
    payload = data or {}
    major_text = payload.get("major_text")
    if major_text is None:
        major_text = payload.get("major")
    return GraduateParsedApplication(
        raw=str(payload.get("raw") or raw_comment or ""),
        name=payload.get("name"),
        major_text=major_text,
        admission_type=payload.get("admission_type"),
        admission_type_raw=payload.get("admission_type_raw"),
        major_code_candidates=list(payload.get("major_code_candidates") or []),
        parse_errors=list(payload.get("parse_errors") or []),
    )


def comment_hash_matches(stored: dict[str, Any] | None, comment: str) -> bool:
    if not stored:
        return False
    stored_hash = stored.get("_comment_hash")
    if not stored_hash:
        return False
    return str(stored_hash) == compute_comment_hash(comment)


def parsed_needs_ai_fallback(stored: dict[str, Any] | None) -> bool:
    """Conservative rematch AI: only when missing or explicitly unparseable."""
    if not stored:
        return True
    errors = [str(e) for e in (stored.get("parse_errors") or [])]
    if any("unable to parse" in e for e in errors):
        return True
    return not stored_parsed_has_fields(stored)


def stored_parsed_has_fields(stored: dict[str, Any] | None) -> bool:
    """True when stored parse has at least one usable applicant field."""
    if not stored:
        return False
    return any(
        stored.get(key)
        for key in (
            "name",
            "student_id",
            "notice_no",
            "major",
            "academy",
            "major_text",
            "admission_type",
            "admission_type_raw",
            "major_code_candidates",
            "notice_no_candidates",
        )
    )


def can_reuse_stored_parsed(
    stored: dict[str, Any] | None, comment: str
) -> bool:
    """Reuse when hash matches, or legacy usable parse without hash metadata."""
    if not stored:
        return False
    if parsed_needs_ai_fallback(stored):
        return False
    if comment_hash_matches(stored, comment):
        return True
    # Pre-v0.4.17 rows: no hash, but fields are still worth rematching.
    if not stored.get("_comment_hash") and stored_parsed_has_fields(stored):
        return True
    return False


def strip_internal_parsed_keys(data: dict[str, Any] | None) -> dict[str, Any]:
    """Remove internal metadata before user-facing notifications."""
    if not data:
        return {}
    return {k: v for k, v in data.items() if not str(k).startswith("_")}


def attach_parsed_meta(
    data: dict[str, Any],
    *,
    comment: str,
    profile: str,
) -> dict[str, Any]:
    out = dict(data)
    out["_comment_hash"] = compute_comment_hash(comment)
    out["_parser_version"] = PARSER_VERSION
    out["_profile"] = profile
    return out
