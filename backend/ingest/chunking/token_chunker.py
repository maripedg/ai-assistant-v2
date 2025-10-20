"""Token-based chunker (whitespace tokenizer).

Purpose
- Split input text into token windows with fractional overlap (0.0–0.5).

Contract
- export: chunk_text_by_tokens(text: str, max_tokens: int, overlap: float) -> list[str]
"""

from __future__ import annotations

from typing import List


def _clamp(v: float, lo: float, hi: float) -> float:
    return hi if v > hi else lo if v < lo else v


def chunk_text_by_tokens(text: str, max_tokens: int, overlap: float) -> List[str]:
    """Split `text` into token windows.

    Rules
    - Tokenizer is whitespace-based.
    - `overlap` is a fraction in [0.0, 0.5].
    - Trims each chunk and drops empties.
    - Deterministic, no side effects.
    """
    if max_tokens <= 0:
        return []
    tokens = [t for t in text.split() if t]
    if not tokens:
        return []

    ov = _clamp(overlap, 0.0, 0.5)
    step = int(round(max_tokens * (1.0 - ov)))
    if step <= 0:
        step = 1

    chunks: List[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        window = tokens[i : i + max_tokens]
        if not window:
            break
        chunk = " ".join(window).strip()
        if chunk:
            chunks.append(chunk)
        if i + max_tokens >= n:
            break
        i += step
    return chunks
