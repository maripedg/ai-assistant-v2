# Streamlit Frontend Docs
Last updated: 2025-11-07

Documentation lives in `frontend/streamlit/docs/` and mirrors the main code folders:

```
app/                     # Views, components, services (namespaced under app.*)
app_config/              # .env loader (get_config)
app/state/               # Feature-specific state helpers
data/                    # Local credentials + feedback stores (JSON/CSV)
services/                # Deprecated shim -> use app/services/*
docs/                    # These files
tests/                   # pytest suites
```

Quick links:
- [Architecture](./ARCHITECTURE.md)
- [Setup & Run](./SETUP_AND_RUN.md)
- [Configuration](./CONFIGURATION.md)
- [State](./STATE.md)
- [Services](./SERVICES.md)
- [Scripts](./SCRIPTS.md)
- [Testing](./TESTING.md)
- [Styleguide](./STYLEGUIDE.md)
- [Runbook](./RUNBOOK.md)
- [Changelog](./CHANGELOG.md)
- Admin features:
  - [Documents & Embeddings](./EMBEDDINGS_ADMIN.md)
  - Feedback History is covered across [Architecture](./ARCHITECTURE.md), [State](./STATE.md), and [Testing](./TESTING.md).
