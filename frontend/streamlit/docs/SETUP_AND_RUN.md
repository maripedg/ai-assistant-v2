# Setup and Run

Overview

- Develop and run the Streamlit frontend locally using a virtual environment and the provided requirements. Configure backend URL and UI settings through .env.

Prerequisites

- Python 3.10+
- PowerShell (Windows) or bash (macOS/Linux)

Install

```bash
cd frontend/streamlit
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
cd frontend/streamlit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Environment

- Copy .env.example to .env and adjust values.

```bash
cp .env.example .env
```

Run

- The main entry is app/main.py.

```bash
streamlit run app/main.py --server.port $FRONTEND_PORT
```

```powershell
streamlit run app/main.py --server.port $env:FRONTEND_PORT
```

Useful Dev Commands

```bash
# format & lint
black .
flake8

# tests
pytest -q
```

```powershell
black .
flake8
pytest -q
```

Quick Links

- Index: ./INDEX.md
- Configuration: ./CONFIGURATION.md

Quick Start: Documents & Embeddings (Admin)

1. Ensure the backend API is running (see backend README) and `GET /healthz` returns ok.
2. Set `DEFAULT_PROFILE`, `UPLOAD_CONCURRENCY`, and auth scope env vars as needed, then `streamlit run app/main.py`.
3. Sign in as an admin user, open **Documents & Embeddings (Admin)**.
4. Select three sample files (PDF/TXT/DOCX), click **Upload**, confirm statuses reach Uploaded with `upload_id` badges.
5. Click **Create Embedding Job**, capture the returned `job_id`, and follow **Go to Assistant** to validate chat is reachable.
