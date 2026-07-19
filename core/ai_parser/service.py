from __future__ import annotations

import asyncio
import inspect
import logging
import re
from typing import Any

from config import PluginSettings
from core.ai_parser.client import (
    call_openai_compatible_json,
    parse_model_response,
    resolve_ai_api_key,
)
from core.ai_parser.models import AiParsedFields, AiParseResult
from core.ai_parser.prompt import build_ai_parse_messages, split_question_answer
from core.ai_parser.validator import validate_ai_fields
from core.decision import DecisionResult
from core.parser import ParsedApplication
from graduate.models import GraduateParsedApplication

logger = logging.getLogger(__name__)

_TEMPLATE_NAME_BAD = frozenset(
    {
        "问题",
        "姓名",
        "问题姓名",
        "问题：姓名",
        "答案",
        "学号",
        "专业",
        "录取号",
    }
)
_AI_MARKERS = ("ai_parse_used", "ai_parse_shadow", "ai_parse_merged")


def _append_marker(parse_errors: list[str], marker: str) -> None:
    if marker not in parse_errors:
        parse_errors.append(marker)


def _append_model_marker(parse_errors: list[str], model: str | None) -> None:
    if not model:
        return
    tag = f"ai_parse_model:{model}"
    if tag not in parse_errors:
        parse_errors.append(tag)


def is_template_misparsed_name(name: str | None) -> bool:
    if not name:
        return False
    compact = name.replace(" ", "").replace("：", "").replace(":", "")
    if name in _TEMPLATE_NAME_BAD or compact in _TEMPLATE_NAME_BAD:
        return True
    if name.startswith("问题"):
        return True
    return False


def is_template_misparsed_major(major: str | None, raw: str | None) -> bool:
    if not major:
        return False
    if "问题" in major or "答案" in major:
        return True
    if raw and major.replace(" ", "") == (raw or "").replace(" ", ""):
        return True
    if re.search(r"[+＋]", major) and re.search(r"\d{6,}", major):
        return True
    return False


def undergrad_parse_incomplete(parsed: ParsedApplication) -> bool:
    if is_template_misparsed_name(parsed.name):
        return True
    if is_template_misparsed_major(parsed.major, parsed.raw):
        return True
    if not parsed.name:
        return True
    if not parsed.student_id and not parsed.notice_no:
        return True
    return False


def grad_parse_incomplete(parsed: GraduateParsedApplication) -> bool:
    if is_template_misparsed_name(parsed.name):
        return True
    if not parsed.name:
        return True
    if not parsed.admission_type:
        return True
    if not parsed.major_text and not parsed.major_code_candidates:
        return True
    return False


def merge_ai_fields_into_undergrad_parsed(
    parsed: ParsedApplication,
    ai_fields: AiParsedFields,
) -> ParsedApplication:
    """Fill missing undergrad fields only; never overwrite student_id/notice_no."""
    if ai_fields.name:
        if not parsed.name or is_template_misparsed_name(parsed.name):
            parsed.name = ai_fields.name
    if ai_fields.student_id and not parsed.student_id:
        parsed.student_id = ai_fields.student_id
    if ai_fields.notice_no and not parsed.notice_no:
        parsed.notice_no = ai_fields.notice_no
        if ai_fields.notice_no not in parsed.notice_no_candidates:
            parsed.notice_no_candidates.append(ai_fields.notice_no)
    if ai_fields.major:
        if not parsed.major or is_template_misparsed_major(parsed.major, parsed.raw):
            parsed.major = ai_fields.major
    if ai_fields.academy and not parsed.academy:
        parsed.academy = ai_fields.academy
    _append_marker(parsed.parse_errors, "ai_parse_used")
    _append_marker(parsed.parse_errors, "ai_parse_merged")
    return parsed


def merge_ai_fields_into_grad_parsed(
    parsed: GraduateParsedApplication,
    ai_fields: AiParsedFields,
) -> GraduateParsedApplication:
    """Fill missing graduate fields only; do not overwrite clear credentials."""
    if ai_fields.name:
        if not parsed.name or is_template_misparsed_name(parsed.name):
            parsed.name = ai_fields.name
    if ai_fields.major:
        if not parsed.major_text or is_template_misparsed_major(parsed.major_text, parsed.raw):
            parsed.major_text = ai_fields.major
    if ai_fields.admission_type and not parsed.admission_type:
        parsed.admission_type = ai_fields.admission_type
        if not parsed.admission_type_raw:
            parsed.admission_type_raw = ai_fields.admission_type
    _append_marker(parsed.parse_errors, "ai_parse_used")
    _append_marker(parsed.parse_errors, "ai_parse_merged")
    return parsed


def ai_parse_was_merged(parsed: Any) -> bool:
    errors = getattr(parsed, "parse_errors", None) or []
    return "ai_parse_merged" in errors


def apply_ai_auto_approve_guard(
    decision: DecisionResult,
    parsed: Any,
    *,
    allow_auto_approve: bool,
) -> DecisionResult:
    """When AI merged fields and allow_auto_approve is false, force manual_review."""
    if allow_auto_approve:
        return decision
    if not ai_parse_was_merged(parsed):
        return decision
    if decision.decision != "approve":
        return decision
    decision.decision = "manual_review"
    decision.should_auto_approve = False
    decision.reason = "AI 辅助解析命中强匹配，需人工确认"
    decision.suggestion = "AI 仅作字段抽取；请人工确认后再通过"
    return decision


def _log_ai_result(
    settings: PluginSettings,
    result: AiParseResult,
    *,
    shadow: bool,
    merged: bool,
    backend: str | None = None,
) -> None:
    payload = {
        "ai_parse_used": True,
        "ai_parse_backend": backend or getattr(settings, "ai_parse_backend", None),
        "ok": result.ok,
        "error": result.error,
        "model": result.model,
        "provider_id": (
            result.model.split(":", 1)[1]
            if result.model and str(result.model).startswith("astrbot_default:")
            else None
        ),
        "response_hash": result.raw_response_hash,
        "shadow": shadow,
        "merged": merged,
        "fields": (result.fields.to_log_dict() if result.fields else None),
    }
    if settings.ai_parse_log_raw:
        logger.warning("[ai_parse] ai_parse_log_raw=true (local debug only): %s", payload)
    else:
        logger.info("[ai_parse] %s", payload)


def _normalize_backend(settings: PluginSettings) -> str:
    backend = (getattr(settings, "ai_parse_backend", None) or "").strip()
    if not backend:
        backend = (getattr(settings, "ai_parse_provider", None) or "").strip()
    backend = backend or "openai_compatible"
    if backend not in {"openai_compatible", "astrbot_default"}:
        return "openai_compatible"
    return backend


async def maybe_run_ai_parse(
    settings: PluginSettings,
    *,
    profile: str,
    raw_comment: str,
    parsed: ParsedApplication | GraduateParsedApplication,
    incomplete: bool,
    client_call=None,
    astrbot_context: Any = None,
    umo: str | None = None,
) -> AiParseResult | None:
    """Optionally call AI. Shadow mode records only; non-shadow merges when incomplete.

    Returns AiParseResult when AI was invoked; None when skipped.
    client_call: optional injectable for tests
      sync or async (messages, settings) -> (content_text, model_name)
    """
    if not settings.ai_parse_enabled:
        return None

    shadow = bool(settings.ai_parse_shadow_mode)
    if not shadow and not incomplete:
        return None

    backend = _normalize_backend(settings)
    question, answer = split_question_answer(raw_comment)
    messages = build_ai_parse_messages(
        profile=profile,
        question=question,
        answer=answer,
        max_chars=settings.ai_parse_max_chars,
    )

    # Privacy: never put secrets into messages (asserted by tests).
    joined = "\n".join(m.get("content", "") for m in messages)
    api_key = resolve_ai_api_key(settings.ai_parse_api_key_env)
    if api_key and api_key in joined:
        return AiParseResult(ok=False, error="prompt privacy violation")

    try:
        if client_call is not None:
            maybe = client_call(messages, settings)
            if inspect.isawaitable(maybe):
                content, model_name = await maybe
            else:
                content, model_name = maybe
        elif backend == "astrbot_default":
            from core.ai_parser.astrbot_client import call_astrbot_default_llm

            content, model_name = await call_astrbot_default_llm(
                astrbot_context,
                messages,
                umo=umo,
                timeout_ms=settings.ai_parse_timeout_ms,
                temperature=0.0,
            )
        else:
            if not settings.ai_parse_base_url or not settings.ai_parse_model:
                result = AiParseResult(ok=False, error="ai_parse config incomplete")
                _mark_shadow_or_used(parsed, shadow=shadow, model=None)
                _log_ai_result(
                    settings, result, shadow=shadow, merged=False, backend=backend
                )
                return result
            if not api_key:
                result = AiParseResult(ok=False, error="ai api key missing")
                _mark_shadow_or_used(parsed, shadow=shadow, model=None)
                _log_ai_result(
                    settings, result, shadow=shadow, merged=False, backend=backend
                )
                return result
            content, meta = await asyncio.to_thread(
                lambda: call_openai_compatible_json(
                    base_url=settings.ai_parse_base_url,
                    api_key=api_key,
                    model=settings.ai_parse_model,
                    messages=messages,
                    timeout_ms=settings.ai_parse_timeout_ms,
                    temperature=0.0,
                )
            )
            model_name = str(meta.get("model") or settings.ai_parse_model)
        result = parse_model_response(
            content,
            default_profile=profile,
            model=model_name,
        )
    except Exception as exc:  # noqa: BLE001 — fallback to deterministic parser
        result = AiParseResult(
            ok=False,
            error=str(exc)[:200],
            model=settings.ai_parse_model or backend,
        )
        _mark_shadow_or_used(parsed, shadow=shadow, model=result.model)
        _log_ai_result(settings, result, shadow=shadow, merged=False, backend=backend)
        return result

    if result.ok and result.fields is not None:
        result.fields = validate_ai_fields(
            result.fields,
            question=question,
            answer=answer,
        )

    merged = False
    if shadow:
        _mark_shadow_or_used(parsed, shadow=True, model=result.model)
    elif result.ok and result.fields is not None and incomplete:
        if profile == "graduate" and isinstance(parsed, GraduateParsedApplication):
            merge_ai_fields_into_grad_parsed(parsed, result.fields)
        elif isinstance(parsed, ParsedApplication):
            merge_ai_fields_into_undergrad_parsed(parsed, result.fields)
        _append_model_marker(parsed.parse_errors, result.model)
        merged = True
    else:
        _append_marker(parsed.parse_errors, "ai_parse_used")
        _append_model_marker(parsed.parse_errors, result.model)

    _log_ai_result(settings, result, shadow=shadow, merged=merged, backend=backend)
    return result


def _mark_shadow_or_used(
    parsed: ParsedApplication | GraduateParsedApplication,
    *,
    shadow: bool,
    model: str | None,
) -> None:
    _append_marker(parsed.parse_errors, "ai_parse_used")
    if shadow:
        _append_marker(parsed.parse_errors, "ai_parse_shadow")
    _append_model_marker(parsed.parse_errors, model)
