import sys
from unittest.mock import MagicMock

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.ux_formatter import extract_external_applicant_and_verification


def test_parse_question_answer_uses_answer_segment():
    applicant, verification = extract_external_applicant_and_verification(
        summary="",
        comment="问题：姓名 学号/录取号 专业\n答案：张轩玮，261108002，德语",
        user_id="999",
    )
    assert applicant == "张轩玮 / 261108002 / 德语"
    assert verification == "张轩玮，261108002，德语"
    assert "问题" not in applicant
    assert "问题：姓名" not in applicant


def test_parse_multiple_answer_markers_take_last():
    applicant, verification = extract_external_applicant_and_verification(
        summary="",
        comment="问题：姓名 学号/录取号 专业\n答案：张轩玮，261108002，德语\n回答：王五，261108003，数学",
        user_id="999",
    )
    assert applicant == "王五 / 261108003 / 数学"
    assert verification == "王五，261108003，数学"


def test_parse_pure_text_tokens():
    applicant, verification = extract_external_applicant_and_verification(
        summary="",
        comment="张轩玮 261108002 德语",
        user_id="999",
    )
    assert applicant == "张轩玮 / 261108002 / 德语"
    assert verification == "张轩玮，261108002，德语"


def test_empty_comment_fallback_to_summary():
    applicant, verification = extract_external_applicant_and_verification(
        summary="张三 / 26115002",
        comment="",
        user_id="999",
    )
    assert applicant == "张三 / 26115002"
    assert verification == "张三 / 26115002"


def test_unparseable_label_words_fallback_to_summary():
    # Without answer marker, label words shouldn't be treated as applicant.
    applicant, _ = extract_external_applicant_and_verification(
        summary="张三 / 26115002",
        comment="问题：姓名 学号/录取号 专业",
        user_id="999",
    )
    assert applicant == "张三 / 26115002"

