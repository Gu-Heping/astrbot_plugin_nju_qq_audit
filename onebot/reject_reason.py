from __future__ import annotations

import json


def normalize_qq_reject_reason(reason: str | None) -> str:
    """Remove accidental quote/JSON wrapping before sending to OneBot."""
    text = (reason or "").strip()
    if not text:
        return ""

    while len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        text = text[1:-1].strip()

    if text.startswith('"') and text.endswith('"'):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str):
            text = decoded.strip()

    return text
