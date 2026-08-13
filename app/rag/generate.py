from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import db
from app.bedrock import converse

SYSTEM_PROMPT = (
    "あなたはAWS公式ドキュメントに基づいて質問に答えるアシスタントです。"
    "以下に与えられた抜粋のみを根拠に、日本語で簡潔に回答してください。"
    "抜粋に記載がない内容は推測せず、"
    "「提供されたドキュメントには記載がありません」と回答してください。"
    "回答中で根拠として使った抜粋は [1] [2] のように番号で示してください。"
)

NO_MATCH_ANSWER = "提供されたドキュメントには記載がありません。"


@dataclass
class Source:
    index: int
    service: str
    doc: str
    section: str | None
    page_start: int | None
    source_url: str
    score: float


@dataclass
class GenerateResult:
    answer: str
    sources: list[Source]
    usage: dict[str, Any]


def _build_context(chunks: list[db.SearchResult]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        header = f"[{i}] ({c.service}/{c.doc} — {c.section or '見出しなし'}, p.{c.page_start})"
        parts.append(f"{header}\n{c.content}")
    return "\n\n".join(parts)


def generate_answer(question: str, chunks: list[db.SearchResult]) -> GenerateResult:
    if not chunks:
        return GenerateResult(answer=NO_MATCH_ANSWER, sources=[], usage={})

    context = _build_context(chunks)
    user_text = f"# 参考ドキュメント抜粋\n{context}\n\n# 質問\n{question}"
    result = converse(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"text": user_text}]}],
    )
    sources = [
        Source(
            index=i,
            service=c.service,
            doc=c.doc,
            section=c.section,
            page_start=c.page_start,
            source_url=c.source_url,
            score=c.score,
        )
        for i, c in enumerate(chunks, start=1)
    ]
    return GenerateResult(answer=result["text"], sources=sources, usage=result["usage"])
