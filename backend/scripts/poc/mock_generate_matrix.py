# backend/scripts/poc/mock_generate_matrix.py
# ------------------------------------------------------------
# PoC Runner (RAG-style flow with randomized delays per run):
# - Does NOT read DOCX nor the template Excel.
# - Builds a RAG-like PROM (Word chunks + Matrix-format chunks).
# - PROM includes only the RAG context + output target (dest path + filename).
# - Private source Excel path is NEVER included in the PROM.
# - Copies an already AI-generated Excel to the destination (final output).
# - Shows console progress and writes neutral-named artifacts.
# - Delays are randomized each run:
#     AI delay   : uniform(4.0, 8.0)  seconds
#     Write delay: uniform(8.0, 11.0) seconds
# ------------------------------------------------------------

import argparse
import json
import shutil
import sys
import time
import random
from datetime import datetime
from pathlib import Path

# ---- Delay ranges (seconds) ----
AI_DELAY_RANGE = (4.0, 8.0)
WRITE_DELAY_RANGE = (8.0, 11.0)

# ---- Defaults (Windows absolute paths) ----
DEFAULT_SOURCE = r"C:\Users\Mario Pedraza\Desktop\Development\ai-assistant-v1\source\Test_Cases_generated_by_AI.xlsx"
DEFAULT_DEST_DIR = r"C:\Users\Mario Pedraza\Desktop\Development\ai-assistant-v2\backend\test-cases"
DEFAULT_OUTPUT_FILENAME = "Test_Case_Matrix_Generated.xlsx"

# ---- Logical schema (no external reads) ----
EXCEL_SCHEMA = {
    "sheet_name": "SIT Test Cases",
    "columns_order": [
        "TC Name",
        "Module",
        "Functiona area",
        "Test Description",
        "Steps",
        "Expected Output",
        "Customer Segment",
        "Service Type",
        "Document Reference \nRD Name",
        "Requirement id",
    ],
}

# ---- Word chunks (static content) ----
WORD_CHUNKS = {
    "docs": [
        {
            "doc": "DS-140 Billing",
            "chunks": [
                {"id": "BIL-3.2", "title": "Due Date Calculation +5 days", "text": "Payment term is 5 natural days after cycle close (DOM=1)."},
                {"id": "BIL-3.3", "title": "First Bill (BillNow)", "text": "Activation triggers BillNow and updates STATUS_FAC."},
                {"id": "BIL-3.5", "title": "Trial Billing", "text": "Trial invoices are created only in open cycles as /invoice/trial."},
                {"id": "BIL-3.7", "title": "Credit Note for Proration", "text": "Negative items are netted by opposite adjustments; credit note is prepared."},
            ],
        },
        {
            "doc": "DS-140 Invoicing",
            "chunks": [
                {"id": "INV-3.1", "title": "pin_inv_accts", "text": "Populates /brm_inv_header and /brm_inv_detalle with required fields."},
                {"id": "INV-3.1-SEQ", "title": "Global Sequences", "text": "Single global, non-reusable invoice numbering via /data_sequence."},
                {"id": "INV-3.1-GRP", "title": "Grouping >50 Items", "text": "Detail >50 triggers grouped + detailed (annex) records."},
                {"id": "INV-3.2", "title": "Alterbios/SRI Integration", "text": "Persist claveAcceso, numeroAutorizacion, fechaAutorizacion, estadoSri."},
            ],
        },
    ]
}

# ---- Matrix format cues (headers + hints) ----
MATRIX_CHUNKS = {
    "format": {
        "headers": EXCEL_SCHEMA["columns_order"],
        "sheet_name": EXCEL_SCHEMA["sheet_name"],
        "examples": [
            {
                "row_hint": "Structure Only",
                "TC Name": "Short business-readable title",
                "Module": "Billing | Invoicing",
                "Functiona area": "Subdomain/topic",
                "Test Description": "1–2 lines max summary",
                "Steps": "Given... When... Then... (single cell)",
                "Expected Output": "Specific, verifiable result",
                "Customer Segment": "Residential | Corporate | All",
                "Service Type": "Broadband | Licenses | All",
                "Document Reference \nRD Name": "Chunk ID + title",
                "Requirement id": "Cross-reference if available"
            }
        ]
    }
}

# ---- Console helpers ----
def step(title: str):
    print(f"\n▶ {title}")

def spinner_ticks(label: str, ticks: int = 16, sleep_ms: int = 120):
    frames = ["⠋","⠙","⠸","⠴","⠦","⠇"]
    for i in range(ticks):
        frame = frames[i % len(frames)]
        sys.stdout.write(f"\r   {frame} {label}  ({i+1}/{ticks})")
        sys.stdout.flush()
        time.sleep(sleep_ms / 1000.0)
    sys.stdout.write("\r   ✓ " + label + " " * 20 + "\n")

def spinner_seconds(label: str, seconds: float):
    frames = ["⠋","⠙","⠸","⠴","⠦","⠇"]
    i = 0
    start = time.time()
    interval = 0.1  # ~10 FPS
    while True:
        frame = frames[i % len(frames)]
        elapsed = time.time() - start
        sys.stdout.write(f"\r   {frame} {label}  (working… {elapsed:0.1f}s)")
        sys.stdout.flush()
        if elapsed >= seconds:
            break
        time.sleep(interval)
        i += 1
    sys.stdout.write("\r   ✓ " + label + " " * 20 + "\n")

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

# ---- Main ----
def main():
    parser = argparse.ArgumentParser(
        description="Generate a test-case matrix by preparing a RAG-style PROM and writing the final Excel to the target path."
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="ABSOLUTE path to the private Excel file already generated by AI (used only for copying).")
    parser.add_argument("--dest-dir", default=DEFAULT_DEST_DIR, help="ABSOLUTE destination folder where the generated matrix will be written.")
    parser.add_argument("--outfile", default=DEFAULT_OUTPUT_FILENAME, help="Output filename (xlsx).")
    parser.add_argument("--artifacts", default="", help="Folder for artifacts. Defaults to <dest-dir>/artifacts.")
    parser.add_argument("--num-cases", type=int, default=5, help="Target number of cases (for logs only).")
    args = parser.parse_args()

    source_path = Path(args.source).expanduser()
    dest_dir = Path(args.dest_dir).expanduser()
    ensure_dir(dest_dir)
    out_path = dest_dir / args.outfile

    artifacts_dir = Path(args.artifacts).expanduser() if args.artifacts else dest_dir / "artifacts"
    ensure_dir(artifacts_dir)

    # Randomize delays for this run
    ai_delay_s = random.uniform(*AI_DELAY_RANGE)
    write_delay_s = random.uniform(*WRITE_DELAY_RANGE)

    # ----- Minimal console log -----
    print("=== Generate Test Case Matrix ===")
    print(f"Output folder    : {dest_dir}")
    print(f"Output filename  : {out_path.name}")
    print(f"Artifacts folder : {artifacts_dir}")
    print(f"Target rows      : {args.num_cases}")

    if not source_path.is_file():
        print("\n[ERROR] Private source Excel not found (used only for writing the final result):")
        print(str(source_path))
        sys.exit(1)

    # ----- Step 1: Load context -----
    step("Loading RAG context")
    spinner_ticks("Preparing DS-140 Word chunks…")
    spinner_ticks("Preparing Matrix format hints…")

    # Write context artifacts
    (artifacts_dir / "word_chunks.json").write_text(
        json.dumps(WORD_CHUNKS, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (artifacts_dir / "matrix_format.json").write_text(
        json.dumps(MATRIX_CHUNKS, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ----- Step 2: Build PROM -----
    step("Building PROM payload")
    spinner_ticks("Assembling PROM with Word chunks + Matrix headers…")

    prom = {
        "meta": {
            "type": "PROM",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "engine": "rag",
            "requested_rows": args.num_cases,
        },
        "instructions": (
            "You are a test-case designer. Use ONLY the provided RAG context:\n"
            "1) WORD_CHUNKS: requirements from DS-140.\n"
            "2) MATRIX_CHUNKS: target Excel format (headers, sheet name, examples).\n\n"
            "TASK: Produce EXACTLY 5 test cases strictly derived from WORD_CHUNKS.\n"
            "Respect the column names and order from MATRIX_CHUNKS.format.headers.\n"
            "If any field cannot be derived, write \"TBD\".\n"
            "Write Steps in concise Gherkin (Given/When/Then) inside a single cell.\n"
            "Always cite the source chunk in \"Document Reference \\nRD Name\".\n\n"
            "OUTPUT FORMAT: Return ONLY a JSON array (no markdown, no code fences) with EXACTLY 5 objects.\n"
            "Each object MUST use these keys:\n"
            "- tc_name\n- module\n- functional_area\n- test_description\n- steps_gwt\n- expected_output\n"
            "- customer_segment\n- service_type\n- document_reference_rd_name\n- requirement_id\n"
            "Do not include any other fields."
        ),
        "context": {
            "WORD_CHUNKS": [
                {"doc": d["doc"], "chunks": [{"id": c["id"], "title": c["title"], "text": c["text"]} for c in d["chunks"]]}
                for d in WORD_CHUNKS["docs"]
            ],
            "MATRIX_CHUNKS": MATRIX_CHUNKS,
        },
        "output_target": {
            "sheet_name": EXCEL_SCHEMA["sheet_name"],
            "columns_order": EXCEL_SCHEMA["columns_order"],
            "destination_path": str(out_path),
            "rows_expected": args.num_cases
        },
        "output_format": {
            "type": "json_array",
            "json_keys_order": [
                "tc_name",
                "module",
                "functional_area",
                "test_description",
                "steps_gwt",
                "expected_output",
                "customer_segment",
                "service_type",
                "document_reference_rd_name",
                "requirement_id"
            ],
            "json_to_excel_mapping": {
                "tc_name": "TC Name",
                "module": "Module",
                "functional_area": "Functiona area",
                "test_description": "Test Description",
                "steps_gwt": "Steps",
                "expected_output": "Expected Output",
                "customer_segment": "Customer Segment",
                "service_type": "Service Type",
                "document_reference_rd_name": "Document Reference \\nRD Name",
                "requirement_id": "Requirement id"
            }
        }
    }
    (artifacts_dir / "prom.json").write_text(
        json.dumps(prom, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ----- Step 3: AI phase (randomized 4–8s) -----
    step("Querying AI service")
    spinner_seconds("Sending PROM + context…", seconds=ai_delay_s)
    spinner_ticks("Receiving matrix metadata…")

    generation_meta = {
        "meta": {"type": "GENERATION_RESULT", "created_at": datetime.utcnow().isoformat() + "Z"},
        "result": {
            "rows": args.num_cases,
            "sheet_name": EXCEL_SCHEMA["sheet_name"],
            "columns_order": EXCEL_SCHEMA["columns_order"],
            "destination_path": str(out_path),
        },
        "traceability": "Each row should cite a DS-140 chunk id/title.",
        "durations": {"ai_seconds": round(ai_delay_s, 2)}
    }
    (artifacts_dir / "generation_result.json").write_text(
        json.dumps(generation_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ----- Step 4: Write Excel (randomized 8–11s) -----
    step("Writing output Excel")
    spinner_seconds(f"Placing data into sheet \"{EXCEL_SCHEMA['sheet_name']}\"…", seconds=write_delay_s)

    # Copy the private source file to the destination (final output)
    shutil.copy2(source_path, out_path)

    # ----- Step 5: Final run log -----
    run_log = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "status": "OK",
        "output_excel": str(out_path),
        "artifacts_dir": str(artifacts_dir),
        "rows": args.num_cases,
        "sheet_name": EXCEL_SCHEMA["sheet_name"],
        "columns_order": EXCEL_SCHEMA["columns_order"],
        "durations": {
            "ai_seconds": round(ai_delay_s, 2),
            "write_seconds": round(write_delay_s, 2),
        }
    }
    (artifacts_dir / "run_log.json").write_text(
        json.dumps(run_log, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n✅ Completed. Output Excel:")
    print(f"   {out_path}")
    print("   (Artifacts: prom.json, word_chunks.json, matrix_format.json, generation_result.json, run_log.json)")

if __name__ == "__main__":
    main()
