"""DOCX loader using zipfile + XML (no external docx dependency).

Purpose
- Extract readable text blocks from `word/document.xml`. If `Heading1` paragraphs are present,
  split into sections by Heading1; otherwise return a single document item.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
import zipfile
import xml.etree.ElementTree as ET
from backend.ingest.text_cleaner import clean_text


W_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def _iter_paragraphs(root: ET.Element):
    for p in root.findall(".//w:p", W_NS):
        yield p


def _p_text(p: ET.Element) -> str:
    parts: List[str] = []
    for t in p.findall(".//w:t", W_NS):
        if t.text:
            parts.append(t.text)
    return "".join(parts).strip()


def _p_is_heading1(p: ET.Element) -> bool:
    ppr = p.find("w:pPr", W_NS)
    if ppr is None:
        return False
    style = ppr.find("w:pStyle", W_NS)
    if style is None:
        return False
    return (style.get(f"{{{W_NS['w']}}}val") or "").lower() in {"heading1", "heading 1"}


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    items: List[Dict] = []
    try:
        with zipfile.ZipFile(abs_path) as zf:
            with zf.open("word/document.xml") as f:
                tree = ET.parse(f)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse DOCX: {abs_path}: {exc}") from exc

    root = tree.getroot()
    sections: List[List[str]] = []
    current: List[str] = []
    saw_heading = False

    for p in _iter_paragraphs(root):
        txt = _p_text(p)
        if not txt:
            continue
        if _p_is_heading1(p):
            saw_heading = True
            if current:
                sections.append(current)
            current = [txt]
        else:
            current.append(txt)
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
                    },
                }
            )
        return items

    # One item per Heading1 section
    for sec in sections:
        text = clean_text("\n".join(sec), preserve_tables=False)
        if not text:
            continue
        items.append(
            {
                "text": text,
                "metadata": {
                    "source": abs_path,
                    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
            }
        )
    return items
