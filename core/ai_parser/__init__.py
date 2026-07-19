"""Optional AI JSON parser fallback (field extraction only)."""

from core.ai_parser.models import AiParsedFields, AiParseResult
from core.ai_parser.service import (
    apply_ai_auto_approve_guard,
    merge_ai_fields_into_grad_parsed,
    merge_ai_fields_into_undergrad_parsed,
    maybe_run_ai_parse,
)

__all__ = [
    "AiParsedFields",
    "AiParseResult",
    "apply_ai_auto_approve_guard",
    "merge_ai_fields_into_grad_parsed",
    "merge_ai_fields_into_undergrad_parsed",
    "maybe_run_ai_parse",
]
