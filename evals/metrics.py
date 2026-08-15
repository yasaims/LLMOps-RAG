"""検索・引用まわりの決定的な評価指標。LLM を使わない純粋関数のみ (依存ゼロ)。

既存 CI (`uv sync --frozen`、eval グループなし) でもユニットテストできることを保証するため、
このモジュールは ragas / langchain / boto3 を import しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass
class RetrievalHit:
    """1問の検索結果を判定した結果。"""

    hit_rank: int | None  # 1-indexed。gold が見つからなければ None
    matched_by: str | None  # "content_hash" | "section_page" | None


def evaluate_retrieval(
    retrieved_ids: list[str],
    retrieved_docs: list[str],
    retrieved_sections: list[str | None],
    retrieved_page_starts: list[int | None],
    gold_content_hash: str,
    gold_doc: str,
    gold_section: str | None,
    gold_page_start: int | None,
    gold_page_end: int | None,
) -> RetrievalHit:
    """gold チャンクが検索結果の何位にヒットしたかを判定する。

    主判定は `SearchResult.id == content_hash` (S3 Vectors の vector key は content_hash
    そのもの。⚠️ この判定は VECTOR_STORE=s3vectors 前提。pgvector の id は連番の DB 主キーで
    content_hash ではないため成立しない)。

    副判定として doc/section が一致しページ範囲が重なる場合もヒット扱いにする。
    app/ingestion/chunk.py の window/overlap パラメータを変更すると全チャンクの content_hash
    が変わってしまい、副判定なしでは全問が偽の不合格になるため必須のフォールバックである。
    ⚠️ SearchResult (app/vectorstore/base.py) は page_end を持たないため、retrieved 側は
    page_start のみで判定する — gold のページ範囲 [gold_page_start, gold_page_end] に
    その値が収まっているかを見る。
    """
    for i, rid in enumerate(retrieved_ids, start=1):
        if rid == gold_content_hash:
            return RetrievalHit(hit_rank=i, matched_by="content_hash")

    lo = gold_page_start
    hi = gold_page_end if gold_page_end is not None else gold_page_start
    for i, (doc, section, page) in enumerate(
        zip(retrieved_docs, retrieved_sections, retrieved_page_starts, strict=True), start=1
    ):
        if doc != gold_doc or section != gold_section:
            continue
        if lo is None or page is None:
            continue
        if lo <= page <= hi:
            return RetrievalHit(hit_rank=i, matched_by="section_page")
    return RetrievalHit(hit_rank=None, matched_by=None)


def recall_at_k(hits: list[RetrievalHit], k: int) -> float:
    """gold が上位 k 件に含まれた問の割合。"""
    if not hits:
        return 0.0
    return sum(1 for h in hits if h.hit_rank is not None and h.hit_rank <= k) / len(hits)


def mean_reciprocal_rank(hits: list[RetrievalHit]) -> float:
    """top-k 内にヒットしなかった問は 0 として平均する。"""
    if not hits:
        return 0.0
    total = sum(1.0 / h.hit_rank if h.hit_rank is not None else 0.0 for h in hits)
    return total / len(hits)


def extract_citation_numbers(answer: str) -> set[int]:
    return {int(n) for n in _CITATION_RE.findall(answer)}


def citation_format_valid(answer: str, num_sources: int) -> bool | None:
    """回答文中の `[n]` 引用番号がすべて有効な範囲 (1 <= n <= num_sources) かを判定する。

    出典なし回答 (num_sources == 0、「提供されたドキュメントには記載がありません」等) は
    判定対象外として None を返す — 引用があってはならないケースなので形式チェックの
    分母に含めない。
    """
    if num_sources == 0:
        return None
    numbers = extract_citation_numbers(answer)
    if not numbers:
        return False
    return all(1 <= n <= num_sources for n in numbers)


def mean_citation_format_valid(values: list[bool | None]) -> float:
    applicable = [v for v in values if v is not None]
    if not applicable:
        return 1.0
    return sum(1 for v in applicable if v) / len(applicable)
