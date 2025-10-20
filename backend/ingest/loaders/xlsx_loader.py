"""XLSX loader using zipfile + XML (dimension-based summary).

Purpose
- Produce a concise summary per sheet: sheet name and dimensions (n_rows x n_cols). Avoids heavy parsing.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
import zipfile
import xml.etree.ElementTree as ET
from backend.ingest.text_cleaner import clean_text


def _sheet_dims(xml_bytes: bytes) -> tuple[int, int]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return 0, 0
    dim = root.find(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}dimension")
    if dim is not None:
        ref = dim.get("ref") or ""
        # Examples: "A1:C10" or single cell "A1"
        if ":" in ref:
            _, max_ref = ref.split(":", 1)
        else:
            max_ref = ref
        # Convert column letters to number and row digits to int
        col_letters = "".join(ch for ch in max_ref if ch.isalpha())
        row_digits = "".join(ch for ch in max_ref if ch.isdigit())
        def col_to_num(s: str) -> int:
            n = 0
            for ch in s:
                n = n * 26 + (ord(ch.upper()) - ord('A') + 1)
            return n
        n_cols = col_to_num(col_letters) if col_letters else 0
        n_rows = int(row_digits) if row_digits else 0
        return n_rows, n_cols
    return 0, 0


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    items: List[Dict] = []
    with zipfile.ZipFile(abs_path) as zf:
        # Parse workbook for sheet list
        try:
            wb_xml = zf.read("xl/workbook.xml")
            wb_root = ET.fromstring(wb_xml)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to parse XLSX workbook: {abs_path}: {exc}") from exc

        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheets = wb_root.findall(".//x:sheets/x:sheet", ns)
        for s in sheets:
            name = s.get("name") or "Sheet"
            r_id = s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            # sheetN.xml usually aligns with index order; attempt standard path
            # When relationships differ, a deeper rels parse would be needed; keep simple mapping here.
            # Try by position first
        sheet_files = sorted(
            (n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        )
        if not sheet_files:
            return items
        for sf in sheet_files:
            try:
                s_xml = zf.read(sf)
            except KeyError:
                continue
            n_rows, n_cols = _sheet_dims(s_xml)
            sheet_name = os.path.splitext(os.path.basename(sf))[0]
            text = clean_text(f"Sheet {sheet_name}: size {n_rows} x {n_cols}", preserve_tables=True)
            items.append(
                {
                    "text": text,
                    "metadata": {
                        "source": abs_path,
                        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "sheet_name": sheet_name,
                        "n_rows": n_rows,
                        "n_cols": n_cols,
                    },
                }
            )
    return items
