from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any

from core.ai_parser.models import AiParseResult
from core.ai_parser.schema import extract_json_object, parse_ai_fields_dict


def resolve_ai_api_key(api_key_env: str) -> str:
    if not api_key_env:
        return ""
    return (os.environ.get(api_key_env) or "").strip()


def response_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        raise ValueError("ai_parse_base_url empty")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def call_openai_compatible_json(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_ms: int,
    temperature: float = 0.0,
) -> tuple[str, dict[str, Any]]:
    """POST chat/completions; return (content_text, meta). Raises on HTTP/network errors."""
    if not api_key:
        raise ValueError("ai api key missing")
    if not model:
        raise ValueError("ai_parse_model empty")

    url = _completions_url(base_url)
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    timeout_s = max(0.1, timeout_ms / 1000.0)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw_bytes = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"ai http {exc.code}: {err_body}") from exc
    except Exception as exc:  # noqa: BLE001 — network/timeout surface as parse failure
        raise RuntimeError(f"ai request failed: {type(exc).__name__}") from exc

    if status >= 400:
        raise RuntimeError(f"ai http {status}")

    data = json.loads(raw_bytes.decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("ai empty choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        raise RuntimeError("ai empty content")
    if isinstance(content, list):
        # Some providers return content parts
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        content = "".join(parts)
    text = str(content)
    meta = {
        "model": str(data.get("model") or model),
        "status": status,
    }
    return text, meta


def parse_model_response(
    content: str,
    *,
    default_profile: str,
    model: str | None,
) -> AiParseResult:
    digest = response_hash(content)
    try:
        data = extract_json_object(content)
        fields = parse_ai_fields_dict(data, default_profile=default_profile)
    except (ValueError, json.JSONDecodeError, TypeError) as exc:
        return AiParseResult(
            ok=False,
            error=f"invalid json: {exc}",
            raw_response_hash=digest,
            model=model,
        )
    return AiParseResult(
        ok=True,
        fields=fields,
        error=None,
        raw_response_hash=digest,
        model=model,
    )
