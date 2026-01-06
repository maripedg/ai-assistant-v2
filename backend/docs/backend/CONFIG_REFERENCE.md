# Config Reference
Last updated: 2025-12-30

Configuration is resolved by [backend/app/deps.py](../../backend/app/deps.py). The loader:
1. Reads `.env` (order: `$APP_ENV_FILE`, repo `.env`, `backend/.env`).
2. Loads YAML from `backend/config/app.yaml` and `backend/config/providers.yaml` unless overridden via `APP_CONFIG_PATH` / `PROVIDERS_CONFIG_PATH`.
3. Exposes merged settings through `settings.app` and helper functions such as `jwt_secret()`, `max_upload_mb()`, and `allow_mime()`.

## `config/app.yaml`
| Section | Highlights |
| --- | --- |
| `server` | Bind address plus CORS allow lists. |
| `retrieval` | `top_k`, `score_mode`, `distance`, short-query overrides, hybrid gates, and prompts. Controls rag/hybrid/fallback behaviour and the `X-Answer-Mode` header. |
| `embeddings` | Active profile, alias view name (`embeddings.alias.name`), domain overrides (`embeddings.domains.<key>.alias_name` for `X-RAG-Domain` overrides, `.index_name` for ingest), chunker specs, dedupe rules, batching, OCR. |
| `prompts` | `rag`, `hybrid`, `fallback` system prompts + `no_context_token`. |
| `features` | Flags for `users_api` and `feedback_api`. |
| `storage` | Mode (`db`/`json`) for users + feedback and optional dual-write. |
| `auth` | Local auth mode, password hashing algorithm, and invite defaults. |
| `database` | SQLAlchemy pool tuning; DSN is usually derived from env. |

## `config/providers.yaml`
| Path | Purpose / Env |
| --- | --- |
| `provider`, `vector_store` | Active provider namespaces (defaults: `oci`). |
| `oci.*` | Endpoints, compartment OCIDs, auth file/profile for embeddings and chat models. Override with `OCI_*` env keys. |
| `oraclevs` | Oracle DSN/user/password, logical table names, and distance metric. Backed by `DB_*` and `ORACLEVS_TABLE`. |

## Environment Keys (Quick Reference)
### Embedding targets
- `embeddings.domains.<domain_key>.index_name` — physical table to upsert chunks when `--domain-key` is used.
- `embeddings.domains.<domain_key>.alias_name` — alias/view updated by `--update-alias` when paired with `--domain-key` and used at query time via `X-RAG-Domain`.

### Database & Oracle
| Key | Description |
| --- | --- |
| `DB_DSN`, `DB_HOST`, `DB_PORT`, `DB_SERVICE` | Oracle connection info (DSN takes precedence). |
| `DB_USER`, `DB_PASSWORD` | Database credentials (SYSDBA supported for bootstrap). |
| `ORACLEVS_TABLE` | Logical base name for vector tables (`<alias>_vN`). |
| `MAX_UPLOAD_MB` | Upload size limit (defaults to 100 MB). `max_upload_bytes()` multiplies by 1024. |

### OCI Generative AI
| Key | Description |
| --- | --- |
| `OCI_REGION`, `OCI_GENAI_ENDPOINT` | Region + inference endpoint. |
| `OCI_COMPARTMENT_OCID` | Compartment containing models. |
| `OCI_AUTH_MODE` | `config_file` or `instance_principal`. |
| `OCI_CONFIG_PATH`, `OCI_CONFIG_PROFILE` | OCI CLI config for SDK auth. Overridden automatically to `oci/config` inside the repo unless explicitly set. |
| `OCI_EMBED_MODEL_ID`, `OCI_LLM_PRIMARY_MODEL_ID`, `OCI_LLM_FALLBACK_MODEL_ID` | Model OCIDs or public aliases. Separate endpoints/compartments can be declared via `OCI_*_ENDPOINT` + `_COMPARTMENT_OCID`. |

### Auth & Feedback
| Key | Description |
| --- | --- |
| `JWT_SECRET`, `SESSION_SECRET` | Signing material for issued tokens (JWT falls back to `SESSION_SECRET`). |
| `JWT_TTL_MIN` | Token lifetime in minutes (default 1440). |
| `AUTH_MODE` | `local`, `sso`, or `hybrid`. Determines password hashing rules when creating users. |
| `AUTH_REQUIRE_SIGNUP_APPROVAL` | If truthy, newly created users start as `invited`. Overridden by payload `status`. |

### Storage & Ingestion
| Key | Description |
| --- | --- |
| `STORAGE_BACKEND`, `STAGING_DIR` | Upload staging provider and directory. |
| `ALLOW_MIME` | CSV or JSON array of allowed MIME types for uploads (lower-case). Defaults to PDF/Office/TXT/HTML. |
| `EMBED_PROFILE`, `EMBED_UPDATE_ALIAS`, `EMBED_EVALUATE` | CLI defaults for embed jobs triggered through APIs or scripts. |
| `RAG_ASSETS_DIR` | Filesystem root for extracted RAG assets (DOCX images). Defaults to `./data/rag-assets` relative to the repo. Created on demand. |
| `DOCX_EXTRACT_IMAGES` | When `true`, saves embedded DOCX images under `<RAG_ASSETS_DIR>/<doc_id>/img_<NNN>.<ext>` and feeds image markers to chunkers. Defaults to `false`. |
| `DOCX_INLINE_FIGURE_PLACEHOLDERS` | When `true`, DOCX chunks include inline `[FIGURE:<figure_id>]` markers where images appear. |
| `DOCX_FIGURE_CHUNKS` | When `true`, creates additional `chunk_type=figure` entries (text-only descriptions) for DOCX images; metadata includes `figure_id`, `parent_chunk_id`, and `image_ref`. |
| `DOCX_IMAGE_DEBUG` | Optional debug flag to log per-image extraction details (rid/target/output); leave off in normal runs. |
| `SP_*` | SharePoint sync service URL, schedule, and timezone hints. |

### Sanitization (see [SANITIZATION.md](./SANITIZATION.md))
`SANITIZE_ENABLED`, `SANITIZE_PROFILE`, `SANITIZE_CONFIG_PATH`, `SANITIZE_PLACEHOLDER_MODE`, `SANITIZE_HASH_SALT`, `SANITIZE_AUDIT_ENABLED`.

### Usage Logging (Oracle)
| Key | Description |
| --- | --- |
| `USAGE_LOG_ENABLED` | When `true`, auth/login/chat flows emit rows to Oracle tables. |
| `USAGE_LOG_SCHEMA` (optional) | Override schema used for `AUTH_LOGINS`, `CHAT_SESSIONS`, `CHAT_INTERACTIONS`. Defaults to the connected user. |

> NOTE: Table creation/grants are handled outside this repo. Ensure `RESP_MODE` exists on `CHAT_INTERACTIONS` for downstream analytics.

## Derived Helpers
- `max_upload_mb()` and `max_upload_bytes()` enforce backend limits that the UI mirrors.
- `allow_mime()` normalises MIME hints so uploads fail fast with `415 Unsupported Media Type`.
- `jwt_secret()` falls back to `SESSION_SECRET` for backwards compatibility—set both in production.

## Tips
- The dependency loader caches YAML; restart the app after editing config.
- Feature flags (`features.users_api`, `features.feedback_api`) control router registration—disable unused routers for lean deployments.
- Dual-write (`storage.dual_write=true`) mirrors DB + JSON writes, useful during migrations; make sure both backends are reachable.
