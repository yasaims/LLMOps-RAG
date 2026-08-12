from app.ingestion.parse import normalize_text, section_for_page


def test_normalize_text_removes_page_number_lines_and_collapses_whitespace():
    raw = "Amazon Bedrock\n\n  42  \nThis   is   a   line.\n"
    assert normalize_text(raw) == "Amazon Bedrock\nThis is a line."


def test_normalize_text_drops_blank_lines():
    raw = "Line one\n\n\nLine two"
    assert normalize_text(raw) == "Line one\nLine two"


def test_section_for_page_returns_deepest_active_section():
    toc = [
        (0, "Overview"),
        (5, "Overview > Quickstart"),
        (10, "Models"),
    ]
    assert section_for_page(toc, 0) == "Overview"
    assert section_for_page(toc, 4) == "Overview"
    assert section_for_page(toc, 5) == "Overview > Quickstart"
    assert section_for_page(toc, 9) == "Overview > Quickstart"
    assert section_for_page(toc, 10) == "Models"
    assert section_for_page(toc, 100) == "Models"


def test_section_for_page_before_first_entry_is_none():
    toc = [(3, "Getting started")]
    assert section_for_page(toc, 0) is None


def test_section_for_page_empty_toc_is_none():
    assert section_for_page([], 0) is None
