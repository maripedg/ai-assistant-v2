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

