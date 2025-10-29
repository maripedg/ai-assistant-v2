# Testing

Overview

- Use pytest for unit tests. Focus on service functions and view helpers; for UI-heavy flows, isolate logic into functions where possible.

Running Tests

```bash
cd frontend/streamlit
pytest -q
```

```powershell
cd frontend/streamlit
pytest -q
```

Fixtures & Coverage

- Mock requests in services/api_client.py using responses or monkeypatch.
- Validate state/session.py behaviors (init_session, add_history).
- Ensure storage helpers read/write temporary directories.

UI Guidance

- For Streamlit rendering, test pure logic paths and avoid asserting on HTML. Prefer unit testing the data contract between services and views.

Quick Links

- Index: ./INDEX.md
- Styleguide: ./STYLEGUIDE.md

Manual Checklist - Documents & Embeddings (Admin)

- Upload three files (PDF, TXT, DOCX). Each should reach Uploaded status with a visible `upload_id`.
- Create an embedding job using the staged `upload_ids`; confirm toast shows the returned `job_id`.
- Attempt to upload an unsupported file (e.g., `.exe`) and verify the 415 message.
- Attempt to upload a file larger than backend limit and confirm the 413 guidance.
- Log in as a non-admin and confirm the Admin page shows the access-restricted notice.
