# Styleguide

Overview

- Consistent Python style and Streamlit UI patterns improve readability and UX.

Python

- Use Black and Flake8; keep functions small and testable.
- Avoid side effects at import time; prefer explicit init at app startup.

Streamlit

- Keep layout simple; use sidebar for navigation and account controls.
- Derive all configuration from get_config() and avoid hardcoding URLs.
- Prefer idempotent UI: rerun on state changes; keep state keys centralized in state/session.py.

Components & Naming

- views/<feature>/__init__.py should expose render(...).
- services/* expose focused functions and keep I/O boundaries thin.

Error Handling

- Catch request exceptions in api_client and return user-friendly messages.
- Log or surface actionable guidance in Status view.

Chat Answer Microcopy & Styling

- Mode chip label: "[ICON] MODE" with subtle border colors (rag=blue, hybrid=purple, fallback=amber, direct=green).
- Tooltip copy:
  - rag: "Answer grounded on your documents"
  - hybrid: "Combined documents + model judgment"
  - fallback: "No sufficient evidence; controlled backup answer"
  - direct: "Answered without retrieval"
- Summary line: "Mode: {mode}. Evidence: {n}. Confidence: {bucket}."
- Answer content renders above the summary card using Markdown. Precedence: answer -> answer2 -> answer3 -> placeholder "No answer content returned." Encourage short paragraphs, bullets for procedures, and fenced code blocks for snippets.
- Evidence list title: "Sources ({n})". Add "Used a subset of available context." when sources_used == "partial".
- Hide the entire Sources section when mode is fallback, gate_failed is true, or sim_max < threshold_low. In that case, show "No sources displayed because they did not meet the quality threshold." inside the Why panel.
- Confidence bar buckets: Low (< threshold_low), Medium (between thresholds), High (>= threshold_high). Clamp 0-100%.
- Snippet copy stays under 300 characters, no absolute paths, display file name and doc_id.

Message Layout (Chat)

- Each assistant turn renders the originating question first as a right-aligned bubble (max-width 75%) with soft blue background and rounded corners.
- The bubble may include inline emphasis (`**bold**`, `_italics_`, `` `code` ``) and shows a muted timestamp underneath, aligned to the right.
- The assistant answer follows immediately below as Markdown, then the mode chip, decision summary, evidence cards (when allowed), and the Why panel.
- Hide the Sources section when the fallback/gate/threshold rules short-circuit; still render the question bubble and answer to keep context clear.
- Keep the bubble CSS scoped under `.aiv2-chat` to avoid bleeding styles into other Streamlit components.
- Optional debug: when `DEBUG_CHAT_UI=true`, log question length and surface a "Debug: Question" expander with the text preview.
- Render each history item inside its own container; add a subtle horizontal divider between messages when helpful for scanability.

Admin Microcopy

- Upload success toast: "Uploaded {filename} (id {upload_id})"
- Upload error (415): "File type not allowed. Try PDF, DOCX, PPTX, XLSX, TXT, or HTML."
- Upload error (413): "File exceeds backend limit. Split the document and retry."
- Job success: "Embedding job {job_id} created. Continue in Assistant."
- Job error: "Job creation failed. Check backend logs and retry."

Quick Links

- Index: ./INDEX.md
- Testing: ./TESTING.md
