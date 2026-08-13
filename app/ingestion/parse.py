"""PDF から (ページ番号, 見出しセクション, 本文) を抽出するモジュール。

見出し階層は PDF のブックマーク (outline) から取得する。ページ本文から
セクションを推測するのではなく、outline の開始ページを基準に「そのページ
が属する最も深いセクション」を逆引きする。
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

_WS_RE = re.compile(r"[ \t]+")
_PAGE_NUM_LINE_RE = re.compile(r"^\s*\d+\s*$")


@dataclass
class PageText:
    page_no: int  # 1-indexed
    section: str | None
    text: str


def normalize_text(text: str) -> str:
    """ヘッダ/フッタ・ページ番号のみの行を除去し、空白を正規化する。"""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _PAGE_NUM_LINE_RE.match(line):
            continue
        lines.append(_WS_RE.sub(" ", line))
    return "\n".join(lines)


def flatten_outline(reader: PdfReader) -> list[tuple[int, str]]:
    """outline を (開始ページ番号(0始まり), 見出しパス) の昇順リストに変換する。"""
    entries: list[tuple[int, str]] = []

    def walk(items: list, path: list[str]) -> None:
        i = 0
        while i < len(items):
            item = items[i]
            if isinstance(item, list):
                walk(item, path)
                i += 1
                continue
            try:
                page_no = reader.get_destination_page_number(item)
            except Exception:
                i += 1
                continue
            title = str(item.title).strip()
            new_path = [*path, title]
            entries.append((page_no, " > ".join(new_path)))
            if i + 1 < len(items) and isinstance(items[i + 1], list):
                walk(items[i + 1], new_path)
                i += 2
            else:
                i += 1

    walk(reader.outline, [])
    entries.sort(key=lambda e: e[0])
    return entries


def section_for_page(toc: list[tuple[int, str]], page_idx: int) -> str | None:
    """0始まりページ番号に対応する見出しパスを toc から逆引きする。"""
    if not toc:
        return None
    pages = [p for p, _ in toc]
    i = bisect_right(pages, page_idx) - 1
    if i < 0:
        return None
    return toc[i][1]


def extract_pages(pdf_path: Path) -> Iterator[PageText]:
    reader = PdfReader(pdf_path)
    toc = flatten_outline(reader)
    for page_idx, page in enumerate(reader.pages):
        raw_text = page.extract_text() or ""
        text = normalize_text(raw_text)
        if not text:
            continue
        yield PageText(
            page_no=page_idx + 1,
            section=section_for_page(toc, page_idx),
            text=text,
        )
