# Styleguide

Overview

- Consistent Python style and Streamlit UI patterns improve readability and UX.

Python

- Use Black and Flake8; keep functions small and testable.
- Avoid side effects in import time; prefer explicit init at app startup.

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

Quick Links

- Index: ./INDEX.md
- Testing: ./TESTING.md

