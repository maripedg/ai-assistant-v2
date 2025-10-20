"""HTML loader using stdlib html.parser.

Purpose
- Extract readable text and split by top‑level sections (h1/h2) when present, capturing `section_path`.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
from html.parser import HTMLParser
from backend.ingest.text_cleaner import clean_text


class _TextSectionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_path: List[str] = []
        self.sections: List[Dict] = []  # {path: "h1>h2", text: str}
        self._buffer: List[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: D401
        if tag in {"h1", "h2"}:
            self._flush_section()
            self.current_path.append(tag)

    def handle_endtag(self, tag):  # noqa: D401
        if tag in {"h1", "h2"}:
            # finalize heading in buffer as a line break
            self._buffer.append("\n")
            # Keep path to build section marker; don't pop to allow cumulative path

    def handle_data(self, data):  # noqa: D401
        if data and data.strip():
            self._buffer.append(data)

    def _flush_section(self):
        text = " ".join(self._buffer).replace("\r", "\n").strip()
        if text:
            path = ">".join(self.current_path) if self.current_path else ""
            self.sections.append({"path": path, "text": text})
        self._buffer = []

    def close(self):  # noqa: D401
        self._flush_section()
        return super().close()


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    parser = _TextSectionParser()
    parser.feed(html)
    parser.close()

    items: List[Dict] = []
    if not parser.sections:
        text = clean_text(" ".join(html.split()), preserve_tables=False)
        if text:
            items.append(
                {
                    "text": text,
                    "metadata": {
                        "source": abs_path,
                        "content_type": "text/html",
                        "section_path": "",
                    },
                }
            )
        return items

    for sec in parser.sections:
        text = clean_text(sec.get("text", ""), preserve_tables=False)
        if not text:
            continue
        items.append(
            {
                "text": text,
                "metadata": {
                    "source": abs_path,
                    "content_type": "text/html",
                    "section_path": sec.get("path", ""),
                },
            }
        )
    return items
