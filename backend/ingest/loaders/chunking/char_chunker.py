"""Character-based chunker.

Purpose
- Split input text into fixed-size character windows with overlap.

Contract
- export: chunk_text(text: str, size: int, overlap: int) -> list[str]
"""

from __future__ import annotations

from typing import List


def chunk_text(text: str, size: int, overlap: int) -> List[str]:
    """Split `text` into character windows with `overlap`.

    Rules
    - Enforces 0 <= overlap < size. If invalid, clamps to valid range.
    - Trims whitespace for each chunk and drops empties.
    - Deterministic, no side effects.
    """
    if size <= 0:
        return []
    if overlap < 0:
        overlap = 0
    if overlap >= size:
        overlap = max(0, size - 1)

    chunks: List[str] = []
    n = len(text)
    if n == 0:
        return []

    step = size - overlap if size - overlap > 0 else 1
    for start in range(0, n, step):
        end = start + size
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
    return chunks
