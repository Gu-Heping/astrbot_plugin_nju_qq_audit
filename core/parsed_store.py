"""Store/reuse parsed application fields across rematch without re-calling AI."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from core.normalize import normalize_whitespace
from core.parser import ParsedApplication
from graduate.models import GraduateParsedApplication

PARSER_VERSION = "v0.4.17"

# Strip spaces around separators so「何聿璿+261」and「何聿璿 + 261」hash equal.
_HASH_SEP = re.compile(r"\s*([+＋/／,，、|;；：:（）()])\s*")


def normalize_comment_for_hash(comment: str) -> str:
    """Normalize comment for revision identity (whitespace-insensitive around seps)."""
    text = normalize_whitespace(comment or "")
    text = _HASH_SEP.sub(r"\1", text)
    return text


def compute_comment_hash(comment: str) -> str:
    text = normalize_comment_for_hash(comment)
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


def is_same_comment_revision(
    stored: dict[str, Any] | None,
    comment: str,
    *,
    allow_unhashed_without_raw: bool = False,
) -> bool:
    """True when stored metadata refers to the same answer revision as comment."""
    if not stored:
        return False
    if comment_hash_matches(stored, comment):
        return True
    if not stored.get("_comment_hash"):
        raw = str(stored.get("raw") or "")
        if raw:
            return normalize_comment_for_hash(raw) == normalize_comment_for_hash(
                comment
            )
        return bool(allow_unhashed_without_raw)
    return False


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


_AI_ATTEMPTED_MARKERS = ("ai_parse_used", "ai_parse_merged", "ai_parse_shadow")


def ai_parse_already_attempted(stored: dict[str, Any] | None) -> bool:
    """True when this revision already invoked AI (success, shadow, or empty)."""
    if not stored:
        return False
    errors = [str(e) for e in (stored.get("parse_errors") or [])]
    return any(marker in errors for marker in _AI_ATTEMPTED_MARKERS)


def carry_ai_attempt_markers(parsed: Any, source: dict[str, Any] | None) -> None:
    """Preserve AI attempt markers onto a re-parsed object for the same revision.

    Rematch may re-run deterministic parse without calling AI; without carrying
    markers, the next rematch would think AI was never attempted.
    """
    if not source or not ai_parse_already_attempted(source):
        return
    errors = getattr(parsed, "parse_errors", None)
    if errors is None:
        return
    for item in source.get("parse_errors") or []:
        text = str(item)
        if text.startswith("ai_parse_model:") or any(
            marker in text for marker in _AI_ATTEMPTED_MARKERS
        ):
            if text not in errors:
                errors.append(text)


def parsed_needs_ai_fallback(stored: dict[str, Any] | None) -> bool:
    """True when rematch may call AI for this stored row.

    - Missing parse / no fields and never attempted AI → allow rematch AI.
    - Usable fields present → no AI.
    - AI already attempted for this revision (even with no fields) → no AI.
    """
    if not stored:
        return True
    if stored_parsed_has_fields(stored):
        return False
    if ai_parse_already_attempted(stored):
        return False
    return True


def can_reuse_stored_parsed(
    stored: dict[str, Any] | None,
    comment: str,
    *,
    allow_unhashed_without_raw: bool = False,
) -> bool:
    """Reuse when hash matches, or legacy usable parse for the same comment.

    ``allow_unhashed_without_raw`` is for rematch of pre-hash rows that lack
    ``raw``; never enable it when the caller may pass a *changed* comment
    (reapply / supersede / failed retry).
    """
    if not stored or not stored_parsed_has_fields(stored):
        return False
    if comment_hash_matches(stored, comment):
        return True
    if not stored.get("_comment_hash"):
        raw = str(stored.get("raw") or "")
        if raw:
            return normalize_comment_for_hash(raw) == normalize_comment_for_hash(
                comment
            )
        return bool(allow_unhashed_without_raw)
    return False


def strip_internal_parsed_keys(data: dict[str, Any] | None) -> dict[str, Any]:
    """Remove internal metadata before user-facing notifications."""
    if not data:
        return {}
    return {k: v for k, v in data.items() if not str(k).startswith("_")}


def fill_undergrad_gaps_from_stored(
    fresh: ParsedApplication, stored: ParsedApplication
) -> ParsedApplication:
    """Keep deterministic fields; fill only missing slots from stored (e.g. AI)."""
    if not fresh.name and stored.name:
        fresh.name = stored.name
    if not fresh.student_id and stored.student_id:
        fresh.student_id = stored.student_id
    if not fresh.notice_no and stored.notice_no:
        fresh.notice_no = stored.notice_no
    if not fresh.major and stored.major:
        fresh.major = stored.major
    if not fresh.academy and stored.academy:
        fresh.academy = stored.academy
    if not fresh.notice_no_candidates and stored.notice_no_candidates:
        fresh.notice_no_candidates = list(stored.notice_no_candidates)
    for err in stored.parse_errors or []:
        text = str(err)
        if text not in fresh.parse_errors:
            if text.startswith("ai_parse_model:") or any(
                marker in text for marker in _AI_ATTEMPTED_MARKERS
            ):
                fresh.parse_errors.append(text)
            elif "unable to parse" in text and not fresh.name and not fresh.student_id:
                fresh.parse_errors.append(text)
    return fresh


def fill_grad_gaps_from_stored(
    fresh: GraduateParsedApplication, stored: GraduateParsedApplication
) -> GraduateParsedApplication:
    """Keep deterministic fields; fill only missing slots from stored (e.g. AI)."""
    if not fresh.name and stored.name:
        fresh.name = stored.name
    if not fresh.major_text and stored.major_text:
        fresh.major_text = stored.major_text
    if not fresh.admission_type and stored.admission_type:
        fresh.admission_type = stored.admission_type
    if not fresh.admission_type_raw and stored.admission_type_raw:
        fresh.admission_type_raw = stored.admission_type_raw
    if not fresh.major_code_candidates and stored.major_code_candidates:
        fresh.major_code_candidates = list(stored.major_code_candidates)
    for err in stored.parse_errors or []:
        text = str(err)
        if text not in fresh.parse_errors:
            if text.startswith("ai_parse_model:") or any(
                marker in text for marker in _AI_ATTEMPTED_MARKERS
            ):
                fresh.parse_errors.append(text)
    return fresh


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
