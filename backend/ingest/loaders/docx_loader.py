"""DOCX loader using zipfile + XML (no external docx dependency).

Purpose
- Extract readable text blocks from `word/document.xml`. If `Heading1` paragraphs are present,
  split into sections by Heading1; otherwise return a single document item.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
from docx import Document
from backend.ingest.text_cleaner import clean_text


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    items: List[Dict] = []
    try:
        doc = Document(abs_path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse DOCX: {abs_path}: {exc}") from exc

    sections: List[List[str]] = []
    current: List[str] = []
    saw_heading = False

    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        style = getattr(p, "style", None)
        style_name = (style.name if style else "").strip().lower()
        if style_name.startswith("toc") or "table of contents" in text.lower():
            continue
        is_heading1 = style_name in {"heading 1", "heading1"}
        if is_heading1:
            saw_heading = True
            if current:
                sections.append(current)
            current = [text]
        else:
            current.append(text)
    if current:
        sections.append(current)

    if not saw_heading:
        # Single document block
        text = clean_text("\n".join("\n".join(sec) for sec in sections), preserve_tables=False)
        if text:
            items.append(
                {
                    "text": text,
                    "metadata": {
                        "source": abs_path,
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "heading_path": [],
                        "section_heading": None,
                    },
                }
            )
        return items

    # One item per Heading1 section
    for sec in sections:
        text = clean_text("\n".join(sec), preserve_tables=False)
        if not text:
            continue
        heading = sec[0] if sec else None
        items.append(
            {
                "text": text,
                "metadata": {
                    "source": abs_path,
                    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "section_heading": heading,
                    "heading_path": [heading] if heading else [],
                },
            }
        )
    return items
