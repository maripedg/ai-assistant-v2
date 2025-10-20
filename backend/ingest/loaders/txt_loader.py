"""Plain text/markdown loader.

Purpose
- Load `.txt`/`.md` and return a single item or paragraph blocks if long.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
from backend.ingest.text_cleaner import clean_text


def _split_paragraphs(text: str) -> list[str]:
    paras: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if buf:
                paras.append(" ".join(buf).strip())
                buf = []
        else:
            buf.append(line.strip())
    if buf:
        paras.append(" ".join(buf).strip())
    return [p for p in paras if p]


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    text = clean_text(content, preserve_tables=False)
    if not text:
        return []

    items: List[Dict] = []
    if len(text) > 4000:
        for para in _split_paragraphs(text):
            if not para:
                continue
            cleaned = clean_text(para, preserve_tables=False)
            if not cleaned:
                continue
            items.append(
                {
                    "text": cleaned,
                    "metadata": {
                        "source": abs_path,
                        "content_type": "text/plain",
                    },
                }
            )
        return items

    items.append(
        {
            "text": text,
            "metadata": {
                "source": abs_path,
                "content_type": "text/plain",
            },
        }
    )
    return items
