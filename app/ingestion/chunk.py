"""セクション境界を尊重したチャンク分割。

セクション境界をまたがないことを第一原則とし、セクション内は
文字数ベースのスライディングウィンドウ (段落境界を優先) で分割する。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.ingestion.parse import PageText

DEFAULT_WINDOW = 1500
DEFAULT_OVERLAP = 200
DEFAULT_MIN_CHUNK_LEN = 100


@dataclass
class Chunk:
    section: str | None
    page_start: int
    page_end: int
    content: str
    content_hash: str = ""


def _window_split(text: str, window: int, overlap: int) -> list[tuple[int, int, str]]:
    if not text:
        return []
    results: list[tuple[int, int, str]] = []
    n = len(text)
    start = 0
    while start < n:
        end = min(start + window, n)
        if end < n:
            break_point = text.rfind("\n\n", start, end)
            if break_point == -1 or break_point <= start + window // 2:
                break_point = text.rfind("\n", start, end)
            if break_point == -1 or break_point <= start + window // 2:
                break_point = text.rfind(" ", start, end)
            if break_point != -1 and break_point > start:
                end = break_point
        chunk_text = text[start:end].strip()
        if chunk_text:
            results.append((start, end, chunk_text))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return results


def _page_for_offset(offsets: list[tuple[int, int, int]], offset: int) -> int:
    for s, e, page_no in offsets:
        if s <= offset < e:
            return page_no
    if offset < offsets[0][0]:
        return offsets[0][2]
    return offsets[-1][2]


def _group_by_section(
    pages: Iterable[PageText],
) -> list[tuple[str | None, list[PageText]]]:
    groups: list[tuple[str | None, list[PageText]]] = []
    for pg in pages:
        if groups and groups[-1][0] == pg.section:
            groups[-1][1].append(pg)
        else:
            groups.append((pg.section, [pg]))
    return groups


def _merge_short_chunks(chunks: list[Chunk], min_len: int) -> list[Chunk]:
    if not chunks:
        return []
    merged: list[Chunk] = [chunks[0]]
    for c in chunks[1:]:
        if len(c.content) < min_len and merged[-1].section == c.section:
            prev = merged[-1]
            merged[-1] = replace(
                prev,
                content=prev.content + "\n" + c.content,
                page_end=max(prev.page_end, c.page_end),
            )
        else:
            merged.append(c)
    return merged


def _with_hashes(chunks: list[Chunk]) -> list[Chunk]:
    out = []
    for c in chunks:
        content_hash = hashlib.sha256(c.content.encode("utf-8")).hexdigest()
        out.append(replace(c, content_hash=content_hash))
    return out


def chunk_pages(
    pages: Iterable[PageText],
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
    min_chunk_len: int = DEFAULT_MIN_CHUNK_LEN,
) -> list[Chunk]:
    chunks: list[Chunk] = []

    for section, pgs in _group_by_section(pages):
        full_text_parts = []
        offsets: list[tuple[int, int, int]] = []
        pos = 0
        for p in pgs:
            full_text_parts.append(p.text)
            offsets.append((pos, pos + len(p.text), p.page_no))
            pos += len(p.text) + 1  # +1 for the joining "\n"
        full_text = "\n".join(full_text_parts)

        for start, end, text in _window_split(full_text, window, overlap):
            page_start = _page_for_offset(offsets, start)
            page_end = _page_for_offset(offsets, max(end - 1, start))
            chunks.append(
                Chunk(section=section, page_start=page_start, page_end=page_end, content=text)
            )

    merged = _merge_short_chunks(chunks, min_chunk_len)
    return _with_hashes(merged)
