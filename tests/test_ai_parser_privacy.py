"""AI parser privacy guarantees."""

from __future__ import annotations

import json
import os

from config import PluginSettings, load_settings, redact_tokens_in_string
from core.ai_parser.prompt import build_ai_parse_messages, split_question_answer
from core.ai_parser.service import maybe_run_ai_parse
from core.parser import ParsedApplication


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_prompt_excludes_flag_token_raw_event_and_roster():
    question, answer = split_question_answer(
        "问题：姓名 学号/录取号 专业\n答案：何聿璿+261880009+技术科学试验班"
    )
    messages = build_ai_parse_messages(
        profile="undergraduate",
        question=question,
        answer=answer,
        max_chars=500,
    )
    blob = json.dumps(messages, ensure_ascii=False)
    assert "raw_event" not in blob
    assert "access_token" not in blob
    assert "Bearer" not in blob
    assert "学生名单" not in blob
    assert "flag=" not in blob
    assert '"flag"' not in blob
    assert "何聿璿" in blob
    assert "261880009" in blob


def test_prompt_does_not_include_student_roster():
    roster_snippet = "张三|261220001|计算机类"
    messages = build_ai_parse_messages(
        profile="undergraduate",
        question="姓名 学号",
        answer="李四 261880009",
        max_chars=500,
    )
    blob = "\n".join(m["content"] for m in messages)
    assert roster_snippet not in blob
    assert "张三" not in blob
    assert "261220001" not in blob


def test_log_redacts_api_key(monkeypatch):
    monkeypatch.setenv("NJU_AUDIT_AI_API_KEY", "sk-secret-test-key-12345")
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_api_key_env": "NJU_AUDIT_AI_API_KEY",
            }
        )
    )
    text = "Authorization: Bearer sk-secret-test-key-12345 failed"
    redacted = redact_tokens_in_string(text, settings)
    assert "sk-secret-test-key-12345" not in redacted
    assert "***" in redacted


def test_service_does_not_put_api_key_in_prompt(monkeypatch):
    monkeypatch.setenv("NJU_AUDIT_AI_API_KEY", "sk-should-never-appear")
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_shadow_mode": True,
                "ai_parse_base_url": "http://example.invalid/v1",
                "ai_parse_model": "m",
                "ai_parse_api_key_env": "NJU_AUDIT_AI_API_KEY",
            }
        )
    )
    captured: list[str] = []

    def fake_client(messages, _settings):
        captured.append(json.dumps(messages, ensure_ascii=False))
        payload = {
            "profile": "undergraduate",
            "name": "何聿璿",
            "student_id": "261880009",
            "major": "技术科学试验班",
            "confidence": 0.9,
            "ambiguous": False,
            "warnings": [],
            "evidence": {
                "name": "何聿璿",
                "student_id": "261880009",
                "major": "技术科学试验班",
            },
        }
        return json.dumps(payload, ensure_ascii=False), "m"

    parsed = ParsedApplication(raw="何聿璿+261880009+技术科学试验班")
    maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment="答案：何聿璿+261880009+技术科学试验班",
        parsed=parsed,
        incomplete=True,
        client_call=fake_client,
    )
    assert captured
    assert "sk-should-never-appear" not in captured[0]
