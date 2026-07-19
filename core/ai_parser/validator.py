from __future__ import annotations

import re

from core.ai_parser.models import AiParsedFields
from core.normalize import normalize_name, normalize_notice_no, normalize_student_id
from graduate.parser import normalize_admission_type

_CHINESE_NAME = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")
_STUDENT_ID_OK = re.compile(r"^(261\d{5,6}|2[0-9]1\d{6})$")
_NOTICE_NO_DIGIT = re.compile(r"^202[56]\d{4}$")
_AMBIGUOUS_TYPE = re.compile(
    r"(硕\s*[/／或]\s*博|博\s*[/／或]\s*硕|"
    r"硕士\s*[/／或]\s*博士|博士\s*[/／或]\s*硕士|硕博|"
    r"硕\s*or\s*博|master\s*[/／]\s*phd)",
    re.IGNORECASE,
)
_TOKEN_SPLIT = re.compile(r"[\s\u3000,，、+/／|]+")

# Longer multi-char tokens first; single-char 硕/博 handled via token candidates.
_MASTER_MULTI = ("硕士生", "硕士", "专硕", "学硕", "master")
_DOCTOR_MULTI = ("博士生", "博士", "直博", "ph.d.", "ph.d", "phd")


def _in_text(needle: str | None, haystack: str) -> bool:
    if not needle:
        return False
    text = needle.strip()
    if not text:
        return False
    if text in haystack:
        return True
    compact_hay = haystack.replace(" ", "").replace("　", "")
    compact_needle = text.replace(" ", "").replace("　", "")
    return bool(compact_needle) and compact_needle in compact_hay


def _evidence_ok(
    field_name: str,
    value: str,
    evidence: dict[str, str | None],
    answer_haystack: str,
) -> bool:
    """Evidence must appear in answer only (never question template)."""
    if not answer_haystack.strip():
        return False
    ev = evidence.get(field_name)
    if ev and _in_text(ev, answer_haystack):
        return True
    return _in_text(value, answer_haystack)


def _type_token_candidates(answer: str, *, exclude_name: str | None = None) -> list[str]:
    """Standalone tokens for admission-type scanning.

    Does not peel trailing 硕/博 from multi-char tokens (avoids treating names
    like 「欧阳博」as doctoral evidence when AI omits a validated name).
    """
    scan = answer or ""
    if exclude_name:
        name = exclude_name.strip()
        if name:
            scan = scan.replace(name, " ", 1)

    candidates: list[str] = []
    for part in _TOKEN_SPLIT.split(scan.strip()):
        part = part.strip()
        if part:
            candidates.append(part)
    return candidates


def _scan_admission_signals(
    answer: str, *, exclude_name: str | None = None
) -> tuple[set[str], bool]:
    """Return ({硕士, 博士}, ambiguous_placeholder_in_answer).

    Single-char 硕/博 inside names (e.g. 王博) are ignored.
    """
    if not answer or not answer.strip():
        return set(), False
    if _AMBIGUOUS_TYPE.search(answer):
        return set(), True

    found: set[str] = set()
    scan = answer
    if exclude_name:
        name = exclude_name.strip()
        if name:
            scan = scan.replace(name, " ", 1)

    compact = scan.replace(" ", "").replace("　", "")
    lower = compact.lower()

    for token in _DOCTOR_MULTI:
        needle = token.lower() if token.isascii() else token
        hay = lower if token.isascii() else compact
        if needle in hay:
            found.add("博士")
            break
    for token in _MASTER_MULTI:
        needle = token.lower() if token.isascii() else token
        hay = lower if token.isascii() else compact
        if needle in hay:
            found.add("硕士")
            break

    for part in _type_token_candidates(answer, exclude_name=exclude_name):
        compact_part = part.replace(" ", "").replace("　", "").lower()
        if compact_part in {"博", "博士", "博士生", "直博", "phd", "ph.d", "ph.d."}:
            found.add("博士")
        elif compact_part in {"硕", "硕士", "硕士生", "专硕", "学硕", "master"}:
            found.add("硕士")

    return found, False


def validate_ai_fields(
    fields: AiParsedFields,
    *,
    question: str,
    answer: str,
) -> AiParsedFields:
    """Drop invalid fields; never raise for field-level failures.

    Evidence is answer-only: question templates (e.g. 硕or博) must not prove fields.
    Empty answer segments yield no evidence.
    """
    _ = question  # intentionally unused for evidence; kept for API compatibility
    answer_haystack = (answer or "").strip()
    warnings = list(fields.warnings)
    evidence = dict(fields.evidence or {})

    def _drop(field_name: str, reason: str) -> None:
        setattr(fields, field_name, None)
        warnings.append(f"drop:{field_name}:{reason}")
        evidence.pop(field_name, None)

    # name
    if fields.name:
        name = normalize_name(fields.name)
        if not _CHINESE_NAME.fullmatch(name):
            _drop("name", "not_chinese_name")
        elif not _evidence_ok("name", name, evidence, answer_haystack):
            _drop("name", "evidence_missing")
        else:
            fields.name = name

    # student_id
    if fields.student_id:
        sid = normalize_student_id(fields.student_id)
        if not _STUDENT_ID_OK.fullmatch(sid):
            _drop("student_id", "invalid_student_id")
        elif not _evidence_ok("student_id", sid, evidence, answer_haystack):
            _drop("student_id", "evidence_missing")
        else:
            fields.student_id = sid

    # notice_no
    if fields.notice_no:
        notice = normalize_notice_no(fields.notice_no)
        if not notice or len(notice) < 4:
            _drop("notice_no", "invalid_notice_no")
        elif notice.isdigit() and not _NOTICE_NO_DIGIT.fullmatch(notice):
            _drop("notice_no", "invalid_notice_no")
        elif not _evidence_ok("notice_no", fields.notice_no, evidence, answer_haystack):
            _drop("notice_no", "evidence_missing")
        else:
            fields.notice_no = notice

    # major
    if fields.major:
        major = fields.major.strip()
        if len(major) < 2:
            _drop("major", "major_too_short")
        elif not _evidence_ok("major", major, evidence, answer_haystack):
            _drop("major", "evidence_missing")
        else:
            fields.major = major

    # academy
    if fields.academy:
        academy = fields.academy.strip()
        if len(academy) < 2:
            _drop("academy", "academy_too_short")
        elif not _evidence_ok("academy", academy, evidence, answer_haystack):
            _drop("academy", "evidence_missing")
        else:
            fields.academy = academy

    # admission_type — evidence must be in answer; placeholders / dual signals → ambiguous
    if fields.admission_type:
        raw_type = fields.admission_type.strip()
        if _AMBIGUOUS_TYPE.search(raw_type) or raw_type in {
            "硕/博",
            "硕博",
            "硕士/博士",
            "硕or博",
            "master/phd",
        }:
            fields.admission_type = None
            fields.ambiguous = True
            warnings.append("admission_type:ambiguous_placeholder")
            evidence.pop("admission_type", None)
        else:
            answer_signals, answer_ambiguous = _scan_admission_signals(
                answer_haystack, exclude_name=fields.name
            )
            if answer_ambiguous:
                fields.admission_type = None
                fields.ambiguous = True
                warnings.append("admission_type:ambiguous_in_answer")
                evidence.pop("admission_type", None)
            elif len(answer_signals) > 1:
                fields.admission_type = None
                fields.ambiguous = True
                warnings.append("admission_type:conflicting_signals")
                evidence.pop("admission_type", None)
            else:
                normalized = normalize_admission_type(raw_type)
                if normalized not in {"硕士", "博士"}:
                    _drop("admission_type", "invalid_admission_type")
                elif normalized not in answer_signals:
                    _drop("admission_type", "evidence_missing")
                else:
                    fields.admission_type = normalized

    fields.warnings = warnings
    fields.evidence = evidence
    return fields
