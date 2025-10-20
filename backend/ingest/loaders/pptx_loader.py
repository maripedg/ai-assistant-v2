"""PPTX loader using zipfile + XML (no external pptx dependency).

Purpose
- Extract slide text (a:t) and optional notes text into a single text block per slide.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
import zipfile
import xml.etree.ElementTree as ET
from backend.ingest.text_cleaner import clean_text


P_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def _text_from_xml(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    parts: List[str] = []
    for t in root.findall(".//a:t", P_NS):
        if t.text:
            parts.append(t.text)
    return "\n".join(x.strip() for x in parts if x.strip())


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    items: List[Dict] = []
    with zipfile.ZipFile(abs_path) as zf:
        # Slides are numbered from 1..N by convention
        slide_names = sorted(
            (n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        )
        for sn in slide_names:
            try:
                slide_xml = zf.read(sn)
            except KeyError:
                continue
            slide_text = _text_from_xml(slide_xml)
            # detect corresponding notes (if any)
            n_base = os.path.basename(sn)  # slideN.xml
            n_idx = os.path.splitext(n_base)[0].replace("slide", "")
            notes_name = f"ppt/notesSlides/notesSlide{n_idx}.xml"
            has_notes = False
            notes_text = ""
            try:
                notes_xml = zf.read(notes_name)
                notes_text = _text_from_xml(notes_xml)
                has_notes = bool(notes_text.strip())
            except KeyError:
                has_notes = False

            text_blocks = [slide_text]
            if has_notes:
                text_blocks.append("Notes:\n" + notes_text)
            text = clean_text("\n\n".join(b for b in text_blocks if b), preserve_tables=False)
            if not text:
                continue
            # slide number
            try:
                slide_number = int(n_idx)
            except Exception:
                slide_number = -1
            items.append(
                {
                    "text": text,
                    "metadata": {
                        "source": abs_path,
                        "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        "slide_number": slide_number,
                        "has_notes": has_notes,
                    },
                }
            )
    return items
