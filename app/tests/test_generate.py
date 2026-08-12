from unittest.mock import patch

from app import db
from app.rag.generate import SYSTEM_PROMPT, generate_answer


def _chunk(id_: int, section: str, content: str, page: int, score: float) -> db.SearchResult:
    return db.SearchResult(
        id=id_,
        section=section,
        content=content,
        page_start=page,
        service="bedrock",
        doc="bedrock-ug",
        source_url="https://example.com/bedrock-ug.pdf",
        score=score,
    )


def test_generate_answer_with_no_chunks_skips_bedrock_call():
    with patch("app.rag.generate.converse") as mock_converse:
        result = generate_answer("質問", [])

    mock_converse.assert_not_called()
    assert result.sources == []
    assert "記載がありません" in result.answer


def test_generate_answer_numbers_sources_in_order_and_calls_converse():
    chunks = [
        _chunk(1, "Overview > Quickstart", "text one", 10, 0.9),
        _chunk(2, "Models", "text two", 20, 0.8),
    ]
    with patch("app.rag.generate.converse") as mock_converse:
        mock_converse.return_value = {
            "text": "回答本文 [1][2]",
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }
        result = generate_answer("質問", chunks)

    mock_converse.assert_called_once()
    _, kwargs = mock_converse.call_args
    assert kwargs["system"] == SYSTEM_PROMPT
    user_text = kwargs["messages"][0]["content"][0]["text"]
    assert "[1]" in user_text
    assert "[2]" in user_text
    assert "text one" in user_text
    assert "text two" in user_text

    assert [s.index for s in result.sources] == [1, 2]
    assert result.sources[0].section == "Overview > Quickstart"
    assert result.sources[1].page_start == 20
    assert result.answer == "回答本文 [1][2]"
    assert result.usage["inputTokens"] == 1
