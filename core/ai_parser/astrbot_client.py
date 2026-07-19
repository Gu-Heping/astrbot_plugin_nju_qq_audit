"""AstrBot default/session LLM adapter for AI JSON parser."""

from __future__ import annotations

import asyncio
from typing import Any

from core.ai_parser.prompt import flatten_messages_for_astrbot


async def resolve_astrbot_provider_id(
    astrbot_context: Any,
    *,
    umo: str | None = None,
) -> str:
    """Resolve current/default chat provider id from AstrBot context."""
    if astrbot_context is None:
        raise RuntimeError("astrbot context missing")

    if umo and hasattr(astrbot_context, "get_current_chat_provider_id"):
        try:
            provider_id = await astrbot_context.get_current_chat_provider_id(umo=umo)
            if provider_id:
                return str(provider_id)
        except Exception:
            pass

    # Fallback: default/using provider without session umo.
    get_using = getattr(astrbot_context, "get_using_provider", None)
    if callable(get_using):
        try:
            try:
                prov = get_using(umo) if umo else get_using()
            except TypeError:
                prov = get_using(umo=umo) if umo else get_using(umo=None)
            if prov is not None:
                meta = getattr(prov, "meta", None)
                if callable(meta):
                    mid = meta().id
                    if mid:
                        return str(mid)
                pid = getattr(prov, "provider_id", None) or getattr(prov, "id", None)
                if pid:
                    return str(pid)
        except Exception as exc:
            raise RuntimeError(f"astrbot provider resolve failed: {type(exc).__name__}") from exc

    raise RuntimeError("astrbot chat provider not found")


async def call_astrbot_default_llm(
    astrbot_context: Any,
    messages: list[dict[str, str]],
    *,
    umo: str | None = None,
    timeout_ms: int = 3000,
    temperature: float = 0.0,
) -> tuple[str, str]:
    """Call AstrBot llm_generate. Returns (content, model_label).

    Does not write conversation history explicitly; uses one-shot prompt/system_prompt.
    """
    if astrbot_context is None:
        raise RuntimeError("astrbot context missing")
    if not hasattr(astrbot_context, "llm_generate"):
        raise RuntimeError("astrbot llm_generate unavailable")

    system_prompt, user_prompt = flatten_messages_for_astrbot(messages)
    provider_id = await resolve_astrbot_provider_id(astrbot_context, umo=umo)
    model_label = f"astrbot_default:{provider_id}"

    async def _generate() -> str:
        # Prefer system_prompt + prompt (AstrBot v4.5.7+). Fall back to single prompt.
        try:
            llm_resp = await astrbot_context.llm_generate(
                chat_provider_id=provider_id,
                system_prompt=system_prompt or None,
                prompt=user_prompt,
                temperature=temperature,
            )
        except TypeError:
            combined = user_prompt
            if system_prompt:
                combined = f"{system_prompt}\n\n{user_prompt}"
            llm_resp = await astrbot_context.llm_generate(
                chat_provider_id=provider_id,
                prompt=combined,
            )
        content = getattr(llm_resp, "completion_text", None)
        if content is None:
            raise RuntimeError("astrbot empty completion_text")
        return str(content)

    timeout_s = max(0.1, timeout_ms / 1000.0)
    try:
        text = await asyncio.wait_for(_generate(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("astrbot llm timeout") from exc
    return text, model_label
