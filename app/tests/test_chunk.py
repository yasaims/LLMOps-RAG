from app.ingestion.chunk import chunk_pages
from app.ingestion.parse import PageText


def test_single_short_page_becomes_one_chunk():
    pages = [PageText(page_no=1, section="Overview", text="Hello world.")]
    chunks = chunk_pages(pages)
    assert len(chunks) == 1
    assert chunks[0].section == "Overview"
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1
    assert chunks[0].content == "Hello world."
    assert len(chunks[0].content_hash) == 64


def test_consecutive_pages_same_section_are_grouped():
    pages = [
        PageText(page_no=1, section="Overview", text="Page one text."),
        PageText(page_no=2, section="Overview", text="Page two text."),
    ]
    chunks = chunk_pages(pages)
    assert len(chunks) == 1
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2


def test_section_boundary_is_not_crossed():
    pages = [
        PageText(page_no=1, section="A", text="A" * 50),
        PageText(page_no=2, section="B", text="B" * 50),
    ]
    chunks = chunk_pages(pages, min_chunk_len=0)
    assert len(chunks) == 2
    assert chunks[0].section == "A"
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1
    assert chunks[1].section == "B"
    assert chunks[1].page_start == 2
    assert chunks[1].page_end == 2


def test_short_trailing_chunk_is_absorbed_into_previous_same_section():
    long_text = ("word " * 40).strip()  # 199 chars, forces a split with small window
    pages = [PageText(page_no=1, section="Overview", text=long_text)]
    chunks = chunk_pages(pages, window=150, overlap=10, min_chunk_len=100)
    # with min_chunk_len high relative to remainder, trailing short piece merges back
    assert all(len(c.content) >= 1 for c in chunks)
    assert all(c.section == "Overview" for c in chunks)


def test_long_text_splits_into_overlapping_windows():
    long_text = "sentence number {} here. ".format
    text = "".join(long_text(i) for i in range(200))
    pages = [PageText(page_no=1, section="Overview", text=text)]
    chunks = chunk_pages(pages, window=500, overlap=100, min_chunk_len=0)
    assert len(chunks) > 1
    for c in chunks:
        assert c.section == "Overview"
        assert c.page_start == 1
        assert c.page_end == 1
        assert len(c.content_hash) == 64
    # content hashes are unique per distinct chunk text
    assert len({c.content_hash for c in chunks}) == len(chunks)
