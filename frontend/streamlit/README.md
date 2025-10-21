# Streamlit Frontend

Quick Start

```bash
cd frontend/streamlit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app/main.py --server.port $FRONTEND_PORT
```

```powershell
cd frontend/streamlit
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
streamlit run app/main.py --server.port $env:FRONTEND_PORT
```

Architecture (overview)

```mermaid
flowchart LR
  UI[app/main.py & views] -->|calls| API[services/api_client.py]
  UI --> STATE[state/session.py]
  UI --> AUTH[services/auth_session.py]
  UI --> STORE[services/storage.py]
```

- Full docs: docs/INDEX.md

