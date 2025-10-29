# Streamlit Frontend Docs

Overview

- Streamlit-based frontend for the AI Assistant. This docs set covers architecture, setup, configuration, state, services, scripts, testing, style, and operations.

Directory Map

```
frontend/streamlit/
  app/                     # UI entrypoints and views
  app_config/              # Environment & config loader
  artifacts-frontend/      # Local artifacts (e.g., inventory, build outputs)
  assets/                  # Static assets
  data/                    # Local data (credentials, feedback)
  docs/                    # This documentation set
  scripts/                 # Helper scripts (optional)
  services/                # API client, auth cookies, storage helpers
  state/                   # Streamlit session-state helpers
  tests/                   # Frontend tests (pytest)
  .env                     # Local environment (not committed)
  .env.example             # Example environment
  requirements.txt         # Frontend dependencies
  README.md                # Quick start & links
```

Quick Links

- Architecture: ./ARCHITECTURE.md
- Setup & Run: ./SETUP_AND_RUN.md
- Configuration: ./CONFIGURATION.md
- State: ./STATE.md
- Services: ./SERVICES.md
- Scripts: ./SCRIPTS.md
- Testing: ./TESTING.md
- Styleguide: ./STYLEGUIDE.md
- Runbook: ./RUNBOOK.md
- Changelog: ./CHANGELOG.md

Admin Features

- Documents & Embeddings (Admin): ./EMBEDDINGS_ADMIN.md
