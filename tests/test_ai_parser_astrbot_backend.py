"""AstrBot default LLM backend for AI JSON parser (v0.4.16)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import load_settings
from core.ai_parser.astrbot_client import call_astrbot_default_llm
from core.ai_parser.prompt import build_ai_parse_messages, flatten_messages_for_astrbot
from core.ai_parser.service import (
    apply_ai_auto_approve_guard,
    maybe_run_ai_parse,
    undergrad_parse_incomplete,
)
from core.decision import make_decision
from core.matcher import match_student
from core.parser import ParsedApplication, parse_application_comment
from data_source.students import Student


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _ai_json(**kwargs) -> str:
    base = {
        "profile": "undergraduate",
        "name": None,
        "student_id": None,
        "notice_no": None,
        "major": None,
        "academy": None,
        "admission_type": None,
        "confidence": 0.95,
        "ambiguous": False,
        "warnings": [],
        "evidence": {},
    }
    base.update(kwargs)
    if not base["evidence"]:
        ev = {}
        for key in (
            "name",
            "student_id",
            "notice_no",
            "major",
            "academy",
            "admission_type",
        ):
            if base.get(key):
                ev[key] = base[key]
        base["evidence"] = ev
    return json.dumps(base, ensure_ascii=False)


def test_backend_defaults_to_openai_compatible():
    settings = load_settings(DummyConfig({}))
    assert settings.ai_parse_backend == "openai_compatible"
    assert settings.ai_parse_enabled is False
    assert settings.ai_parse_shadow_mode is True
    assert settings.ai_parse_allow_auto_approve is False


def test_astrbot_default_does_not_require_url_model_key():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_backend": "astrbot_default",
                "ai_parse_shadow_mode": True,
            }
        )
    )
    assert settings.ai_parse_backend == "astrbot_default"
    assert settings.ai_parse_base_url == ""
    assert settings.ai_parse_model == ""


def test_legacy_ai_parse_provider_alias():
    settings = load_settings(
        DummyConfig({"ai_parse_provider": "astrbot_default"})
    )
    assert settings.ai_parse_backend == "astrbot_default"


@pytest.mark.asyncio
async def test_astrbot_llm_generate_parses_fields():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_backend": "astrbot_default",
                "ai_parse_shadow_mode": False,
            }
        )
    )
    context = MagicMock()
    context.get_current_chat_provider_id = AsyncMock(return_value="prov-1")
    context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text=_ai_json(
                name="何聿璿",
                student_id="261880009",
                major="技术科学试验班",
            )
        )
    )
    parsed = ParsedApplication(raw="何聿璿+261880009+技术科学试验班")
    result = await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment="答案：何聿璿+261880009+技术科学试验班",
        parsed=parsed,
        incomplete=True,
        astrbot_context=context,
        umo="qq:private:1",
    )
    assert result is not None
    assert result.ok
    assert parsed.name == "何聿璿"
    assert parsed.student_id == "261880009"
    assert parsed.major == "技术科学试验班"
    assert result.model == "astrbot_default:prov-1"
    context.llm_generate.assert_awaited()
    call_kwargs = context.llm_generate.await_args.kwargs
    assert call_kwargs.get("chat_provider_id") == "prov-1"
    prompt_blob = json.dumps(call_kwargs, ensure_ascii=False)
    assert "flag" not in prompt_blob or '"flag"' not in prompt_blob
    assert "raw_event" not in prompt_blob
    assert "access_token" not in prompt_blob
    assert "学生名单" not in prompt_blob


@pytest.mark.asyncio
async def test_astrbot_llm_exception_falls_back():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_backend": "astrbot_default",
                "ai_parse_shadow_mode": False,
            }
        )
    )
    context = MagicMock()
    context.get_current_chat_provider_id = AsyncMock(return_value="prov-1")
    context.llm_generate = AsyncMock(side_effect=RuntimeError("boom"))
    parsed = ParsedApplication(raw="何聿璿+261880009+技术科学试验班")
    result = await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment="答案：何聿璿+261880009+技术科学试验班",
        parsed=parsed,
        incomplete=True,
        astrbot_context=context,
    )
    assert result is not None
    assert result.ok is False
    assert "ai_parse_merged" not in parsed.parse_errors
    assert parsed.name is None
    assert parsed.student_id is None


@pytest.mark.asyncio
async def test_astrbot_prompt_privacy_via_flatten():
    messages = build_ai_parse_messages(
        profile="undergraduate",
        question="姓名 学号/录取号 专业",
        answer="何聿璿+261880009+技术科学试验班",
        max_chars=500,
    )
    system, user = flatten_messages_for_astrbot(messages)
    blob = system + "\n" + user
    assert "何聿璿" in blob
    assert "raw_event" not in blob
    assert "access_token" not in blob
    assert "Bearer" not in blob
    assert "学生名单" not in blob
    assert "flag=" not in blob


@pytest.mark.asyncio
async def test_astrbot_shadow_does_not_merge():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_backend": "astrbot_default",
                "ai_parse_shadow_mode": True,
            }
        )
    )
    context = MagicMock()
    context.get_current_chat_provider_id = AsyncMock(return_value="prov-1")
    context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text=_ai_json(
                name="何聿璿",
                student_id="261880009",
                major="技术科学试验班",
            )
        )
    )
    comment = "答案：何聿璿+261880009+技术科学试验班"
    parsed = parse_application_comment(comment)
    snap = (parsed.name, parsed.student_id, parsed.major)
    await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment=comment,
        parsed=parsed,
        incomplete=undergrad_parse_incomplete(parsed),
        astrbot_context=context,
    )
    assert (parsed.name, parsed.student_id, parsed.major) == snap
    assert "ai_parse_shadow" in parsed.parse_errors
    assert "ai_parse_merged" not in parsed.parse_errors


@pytest.mark.asyncio
async def test_astrbot_merged_strong_still_manual_review():
    parsed = ParsedApplication(
        raw="何聿璿+261880009+技术科学试验班",
        name="何聿璿",
        student_id="261880009",
        major="技术科学试验班",
        parse_errors=["ai_parse_used", "ai_parse_merged", "ai_parse_model:astrbot_default:p"],
    )
    students = [
        Student(
            name="何聿璿",
            updated_at="t",
            student_id="261880009",
            major="技术科学试验班",
        )
    ]
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    assert match.strength == "strong"
    assert decision.decision == "manual_review"


@pytest.mark.asyncio
async def test_call_astrbot_default_llm_direct():
    context = MagicMock()
    context.get_current_chat_provider_id = AsyncMock(return_value="p2")
    context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"name":"张三"}')
    )
    messages = build_ai_parse_messages(
        profile="undergraduate",
        question="姓名",
        answer="张三",
        max_chars=100,
    )
    text, model = await call_astrbot_default_llm(
        context, messages, umo="u1", timeout_ms=3000
    )
    assert "张三" in text
    assert model == "astrbot_default:p2"
    context.get_current_chat_provider_id.assert_awaited_once_with(umo="u1")


@pytest.mark.asyncio
async def test_umo_passed_to_provider_resolver():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_backend": "astrbot_default",
                "ai_parse_shadow_mode": True,
            }
        )
    )
    context = MagicMock()
    context.get_current_chat_provider_id = AsyncMock(return_value="session-prov")
    context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text=_ai_json(name="何聿璿"))
    )
    parsed = ParsedApplication(raw="何聿璿+261880009+技术科学试验班")
    await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment="答案：何聿璿+261880009+技术科学试验班",
        parsed=parsed,
        incomplete=True,
        astrbot_context=context,
        umo="qq:group:100",
    )
    context.get_current_chat_provider_id.assert_awaited_once_with(umo="qq:group:100")


@pytest.mark.asyncio
async def test_llm_generate_typeerror_does_not_double_call():
    """Provider TypeError must not trigger a second llm_generate with applicant text."""
    calls: list[dict] = []

    async def boom(*, chat_provider_id, prompt, system_prompt=None, temperature=0.0):
        calls.append(
            {
                "chat_provider_id": chat_provider_id,
                "prompt": prompt,
                "system_prompt": system_prompt,
            }
        )
        raise TypeError("provider internal type error")

    context = MagicMock()
    context.get_current_chat_provider_id = AsyncMock(return_value="p")
    context.llm_generate = boom

    messages = build_ai_parse_messages(
        profile="undergraduate",
        question="姓名",
        answer="何聿璿",
        max_chars=100,
    )
    with pytest.raises(TypeError):
        await call_astrbot_default_llm(context, messages, umo="u", timeout_ms=3000)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_astrbot_error_is_redacted_in_result():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_backend": "astrbot_default",
                "ai_parse_shadow_mode": True,
                "ai_parse_api_key_env": "NJU_AUDIT_AI_API_KEY",
            }
        )
    )
    context = MagicMock()
    context.get_current_chat_provider_id = AsyncMock(return_value="p")
    context.llm_generate = AsyncMock(
        side_effect=RuntimeError(
            "Authorization: Bearer sk-astrbot-secret-key-should-not-leak"
        )
    )
    parsed = ParsedApplication(raw="何聿璿+261880009+技术科学试验班")
    result = await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment="答案：何聿璿+261880009+技术科学试验班",
        parsed=parsed,
        incomplete=True,
        astrbot_context=context,
        umo="u",
    )
    assert result is not None
    assert result.ok is False
    assert "sk-astrbot-secret-key-should-not-leak" not in (result.error or "")
    assert "***" in (result.error or "")
