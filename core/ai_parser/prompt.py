from __future__ import annotations

import re

from core.ai_parser.schema import ai_fields_json_schema_hint
from core.parser import ANSWER_MARKER_PATTERN, extract_answer_segment


_QUESTION_MARKER = re.compile(r"问题\s*[：:]\s*", re.IGNORECASE)


def extract_question_segment(raw: str) -> str:
    """Extract question template text only (never returns answer segment)."""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    answer_matches = list(ANSWER_MARKER_PATTERN.finditer(text))
    if answer_matches:
        before = text[: answer_matches[-1].start()].strip()
    else:
        before = text

    q_match = _QUESTION_MARKER.search(before)
    if q_match:
        return before[q_match.end() :].strip()
    if "问题" in before:
        return before.strip()
    return ""


def build_ai_parse_messages(
    *,
    profile: str,
    question: str,
    answer: str,
    max_chars: int,
) -> list[dict[str, str]]:
    """Build chat messages. Must not include roster, flag, token, or raw_event."""
    q = (question or "")[:max_chars]
    a = (answer or "")[:max_chars]
    system = (
        "你是入群验证字段抽取器。只从问题与答案中抽取结构化字段，"
        "不要判断是否通过或拒绝。只输出一个 JSON object，不要 markdown。"
        f"Schema 示例：{ai_fields_json_schema_hint()}。"
        "admission_type 只能是 硕士、博士 或 null；"
        "若答案是「硕/博」「硕士/博士」等占位，admission_type=null 且 ambiguous=true。"
        "本科考生号是 14 位数字，通常以 26 开头；它不是学号。"
        "不要把考生号填到 student_id。"
        "如果答案中出现 14 位考生号，填 exam_no。"
        "evidence 中每个非空值必须是原文中可找到的子串。"
        "不要编造学号、姓名或专业。"
    )
    user = (
        f"profile: {profile}\n"
        f"question: {q}\n"
        f"answer: {a}\n"
        "只返回 JSON object。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def split_question_answer(raw: str) -> tuple[str, str]:
    """Split QQ verify text into (question, answer).

    When an explicit ``答案：`` marker exists, never fall back to the full raw
    text (empty answer stays empty).

    When a question line exists without ``答案：``, keep newline-separated answer
    text from ``extract_answer_segment``; only treat answer as empty when the
    remainder is missing or identical to the question template.
    """
    text = raw or ""
    question = extract_question_segment(text)
    has_answer_marker = bool(ANSWER_MARKER_PATTERN.search(text))
    extracted = (extract_answer_segment(text) or "").strip()
    if has_answer_marker:
        return question, extracted
    if question:
        if not extracted or extracted == question.strip():
            return question, ""
        return question, extracted
    return question, extracted or text


def flatten_messages_for_astrbot(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Split chat messages into (system_prompt, user_prompt) for AstrBot llm_generate."""
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages:
        role = (message.get("role") or "").strip().lower()
        content = str(message.get("content") or "")
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        else:
            user_parts.append(content)
    return "\n\n".join(system_parts).strip(), "\n\n".join(user_parts).strip()
