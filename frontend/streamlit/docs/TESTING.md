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

