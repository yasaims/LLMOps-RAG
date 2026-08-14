from evals.metrics import (
    RetrievalHit,
    citation_format_valid,
    evaluate_retrieval,
    extract_citation_numbers,
    mean_citation_format_valid,
    mean_reciprocal_rank,
    recall_at_k,
)


def test_evaluate_retrieval_hits_on_content_hash():
    hit = evaluate_retrieval(
        retrieved_ids=["hashA", "hashB", "hashC"],
        retrieved_docs=["bedrock-ug"] * 3,
        retrieved_sections=["Overview"] * 3,
        retrieved_page_starts=[10, 20, 30],
        gold_content_hash="hashB",
        gold_doc="bedrock-ug",
        gold_section="Overview",
        gold_page_start=20,
        gold_page_end=20,
    )
    assert hit.hit_rank == 2
    assert hit.matched_by == "content_hash"


def test_evaluate_retrieval_falls_back_to_section_and_page_overlap():
    # content_hash が変わってしまった (chunk.py のパラメータ変更等) ケースを模す
    hit = evaluate_retrieval(
        retrieved_ids=["new-hash-1", "new-hash-2"],
        retrieved_docs=["bedrock-ug", "bedrock-ug"],
        retrieved_sections=["Overview", "Models"],
        retrieved_page_starts=[41, 50],
        gold_content_hash="old-hash-that-no-longer-exists",
        gold_doc="bedrock-ug",
        gold_section="Overview",
        gold_page_start=40,
        gold_page_end=42,
    )
    assert hit.hit_rank == 1
    assert hit.matched_by == "section_page"


def test_evaluate_retrieval_no_hit():
    hit = evaluate_retrieval(
        retrieved_ids=["a", "b"],
        retrieved_docs=["bedrock-ug", "bedrock-ug"],
        retrieved_sections=["Other", "Other2"],
        retrieved_page_starts=[1, 2],
        gold_content_hash="gold",
        gold_doc="bedrock-ug",
        gold_section="Overview",
        gold_page_start=10,
        gold_page_end=10,
    )
    assert hit.hit_rank is None
    assert hit.matched_by is None


def test_evaluate_retrieval_section_match_but_page_out_of_range_is_not_a_hit():
    hit = evaluate_retrieval(
        retrieved_ids=["x"],
        retrieved_docs=["bedrock-ug"],
        retrieved_sections=["Overview"],
        retrieved_page_starts=[99],
        gold_content_hash="gold",
        gold_doc="bedrock-ug",
        gold_section="Overview",
        gold_page_start=10,
        gold_page_end=12,
    )
    assert hit.hit_rank is None


def test_recall_at_k():
    hits = [
        RetrievalHit(hit_rank=1, matched_by="content_hash"),
        RetrievalHit(hit_rank=3, matched_by="content_hash"),
        RetrievalHit(hit_rank=None, matched_by=None),
    ]
    assert recall_at_k(hits, k=1) == 1 / 3
    assert recall_at_k(hits, k=3) == 2 / 3
    assert recall_at_k(hits, k=5) == 2 / 3


def test_recall_at_k_empty():
    assert recall_at_k([], k=5) == 0.0


def test_mean_reciprocal_rank():
    hits = [
        RetrievalHit(hit_rank=1, matched_by="content_hash"),
        RetrievalHit(hit_rank=2, matched_by="content_hash"),
        RetrievalHit(hit_rank=None, matched_by=None),
    ]
    assert mean_reciprocal_rank(hits) == (1.0 + 0.5 + 0.0) / 3


def test_extract_citation_numbers():
    assert extract_citation_numbers("回答 [1] と [2] を根拠に [1] 再掲") == {1, 2}
    assert extract_citation_numbers("引用なし") == set()


def test_citation_format_valid_no_sources_is_not_applicable():
    assert (
        citation_format_valid("提供されたドキュメントには記載がありません。", num_sources=0) is None
    )


def test_citation_format_valid_missing_citation_is_invalid():
    assert citation_format_valid("根拠を示さない回答。", num_sources=3) is False


def test_citation_format_valid_out_of_range_is_invalid():
    assert citation_format_valid("回答 [4]。", num_sources=3) is False


def test_citation_format_valid_in_range_is_valid():
    assert citation_format_valid("回答 [1][2]。", num_sources=3) is True


def test_mean_citation_format_valid_ignores_not_applicable():
    values = [True, False, None, True]
    assert mean_citation_format_valid(values) == 2 / 3


def test_mean_citation_format_valid_all_not_applicable_defaults_to_one():
    assert mean_citation_format_valid([None, None]) == 1.0
