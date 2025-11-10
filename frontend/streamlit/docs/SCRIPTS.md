# Scripts
Last updated: 2025-11-07

The `frontend/streamlit/scripts/` folder is optional and currently empty. Use it for one-off maintenance utilities (e.g., migrating local credential files, exporting feedback CSVs, seeding demo data).

Guidelines:
- Keep scripts self-contained (no implicit imports from Streamlit’s runtime).
- Accept parameters via `argparse` and print actionable output; don’t rely on Streamlit UI state.
- Document prerequisites at the top of each script so operators know which env variables or files are required.

Example ideas:
- `scripts/users_init.py` – bootstrap `usuarios.json` with a default admin account when running in `AUTH_MODE=local`.
- `scripts/feedback_export.py` – convert `data/feedback/fback.json` into a sanitized CSV for audits.

Remember to commit scripts under version control; avoid storing credentials or secrets in this folder.
