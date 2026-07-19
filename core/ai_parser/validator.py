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

# Longer tokens first for answer scanning.
_MASTER_TOKENS = ("硕士生", "硕士", "专硕", "学硕", "master", "硕")
_DOCTOR_TOKENS = ("博士生", "博士", "直博", "ph.d.", "ph.d", "phd", "博")


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
    ev = evidence.get(field_name)
    if ev and _in_text(ev, answer_haystack):
        return True
    return _in_text(value, answer_haystack)


def _scan_admission_signals(answer: str) -> tuple[set[str], bool]:
    """Return ({硕士, 博士}, ambiguous_placeholder_in_answer)."""
    if not answer:
        return set(), False
    if _AMBIGUOUS_TYPE.search(answer):
        return set(), True

    compact = answer.replace(" ", "").replace("　", "")
    lower = compact.lower()
    found: set[str] = set()

    # Prefer longer matches; mark occupied spans to avoid 博 in 博士 double-count issues
    # by checking tokens in length order on original compact text.
    remaining = compact
    remaining_lower = lower
    for token in _DOCTOR_TOKENS:
        needle = token.lower() if token.isascii() else token
        hay = remaining_lower if token.isascii() else remaining
        if needle in hay:
            found.add("博士")
            break
    for token in _MASTER_TOKENS:
        needle = token.lower() if token.isascii() else token
        hay = remaining_lower if token.isascii() else remaining
        if needle in hay:
            found.add("硕士")
            break
    return found, False


def validate_ai_fields(
    fields: AiParsedFields,
    *,
    question: str,
    answer: str,
) -> AiParsedFields:
    """Drop invalid fields; never raise for field-level failures.

    Evidence is answer-only: question templates (e.g. 硕or博) must not prove fields.
    """
    _ = question  # intentionally unused for evidence; kept for API compatibility
    answer_haystack = answer or ""
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
            answer_signals, answer_ambiguous = _scan_admission_signals(answer_haystack)
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
                elif not answer_signals:
                    _drop("admission_type", "evidence_missing")
                elif normalized not in answer_signals:
                    _drop("admission_type", "evidence_missing")
                elif not (
                    _evidence_ok("admission_type", raw_type, evidence, answer_haystack)
                    or _in_text(normalized, answer_haystack)
                    or any(
                        _in_text(tok, answer_haystack)
                        for tok in (
                            _MASTER_TOKENS if normalized == "硕士" else _DOCTOR_TOKENS
                        )
                    )
                ):
                    _drop("admission_type", "evidence_missing")
                else:
                    fields.admission_type = normalized

    fields.warnings = warnings
    fields.evidence = evidence
    return fields
