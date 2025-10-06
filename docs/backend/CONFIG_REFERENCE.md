# Config Reference

Configuration is loaded by [backend/app/deps.py](../../backend/app/deps.py), which resolves YAML/JSON from `backend/config/` unless overridden by environment variables.

## Loading Rules
- `APP_CONFIG_PATH` and `PROVIDERS_CONFIG_PATH` environment variables can point to alternative YAML files. Relative paths are resolved from the repo root.
- `.env` discovery order: `$APP_ENV_FILE` (if set), `${repo}/.env`, then `backend/.env`.
- `OCI_CONFIG_FILE` and `OCI_CONFIG_PROFILE` default to `oci/config` and `DEFAULT` but may be overridden via `.env`.

## `config/app.yaml`
| Section | Keys | Description |
| --- | --- | --- |
| `server` | `host`, `port`, `cors.allow_*` | FastAPI bind address and CORS whitelists. |
| `retrieval` | `mode`, `top_k`, `distance`, `threshold_low/high`, `score_mode`, nested `thresholds` for raw metrics | Governs similarity search parameters and thresholding. |
| `retrieval.short_query` | `max_tokens`, `threshold_low/high` | Overrides thresholds for terse questions. |
| `retrieval.expansions` | `enabled`, `terms` | Reserved for query expansion (currently unused). |
| `retrieval.hybrid` | `max_context_chars`, `max_chunks`, `min_tokens_per_chunk`, flags for LLM enrichment/citations | Controls context assembly and hybrid behavior. |
| `embeddings.active_profile` | String | Default profile for ingestion jobs. |
| `embeddings.alias` | `name`, `active_index` | Alias/view indirection between physical tables and API. |
| `embeddings.profiles` | Nested profiles (e.g., `legacy_profile`, `standard_profile`) with `index_name`, `chunker`, `distance_metric`, `input_types`, `metadata.keep` | Manifest-driven chunking and storage definitions. |
| `embeddings.batching` | `batch_size`, `workers`, `rate_limit_per_min` | Embed job batching knobs. |
| `embeddings.dedupe` | `by_hash`, `hash_normalization` | Enable/disable duplicate suppression. |
| `prompts` | `no_context_token`, `rag.system`, `hybrid.system`, `fallback.system`, `max_output_tokens` | System prompts injected into LLM calls. |
| `fallback` | `enabled`, `policy` | Advisory flags for future routing policies. |

## `config/providers.yaml`
| Path | Description | Backed by `.env` |
| --- | --- | --- |
| `provider` | Default provider namespace (`oci`). | – |
| `vector_store` | Storage provider (`oci`). | – |
| `oci.endpoint` | OCI GenAI inference endpoint. | `OCI_GENAI_ENDPOINT` |
| `oci.region` | Region code (for logging). | `OCI_REGION` |
| `oci.compartment_id` | Compartment OCID. | `OCI_COMPARTMENT_OCID` |
| `oci.auth_mode` | `config_file` or `instance_principal`. | `OCI_AUTH_MODE` |
| `oci.config_path` / `config_profile` | OCI SDK auth. | `OCI_CONFIG_PATH`, `OCI_CONFIG_PROFILE` |
| `oci.models.embeddings` / `chat` | Backward-compat aliases. | `OCI_EMBED_MODEL_ID`, `OCI_LLM_MODEL_ID` |
| `oci.embeddings` | `model_id`, `endpoint`, `compartment_id`, `auth_file`, `auth_profile`. | `OCI_EMBED_MODEL_ID`, `OCI_GENAI_ENDPOINT`, `OCI_COMPARTMENT_OCID`, `OCI_CONFIG_PATH`, `OCI_CONFIG_PROFILE` |
| `oci.llm_primary` | Primary chat model configuration; `model_id` may be alias or OCID. | `OCI_LLM_PRIMARY_MODEL_ID`, `OCI_LLM_PRIMARY_ENDPOINT`, `OCI_LLM_PRIMARY_COMPARTMENT_OCID`, `OCI_CONFIG_PATH`, `OCI_CONFIG_PROFILE` |
| `oci.llm_fallback` | Fallback chat model configuration (must be OCID). | `OCI_LLM_FALLBACK_MODEL_ID`, `OCI_LLM_FALLBACK_ENDPOINT`, `OCI_LLM_FALLBACK_COMPARTMENT_OCID`, `OCI_CONFIG_PATH`, `OCI_CONFIG_PROFILE` |
| `oraclevs` | Oracle DSN, username, password, and default logical table. | `DB_DSN`, `DB_USER`, `DB_PASSWORD`, `ORACLEVS_TABLE` |

## `.env` Keys (from `backend/.env.example`)
| Key | Purpose |
| --- | --- |
| `DB_DSN` | Oracle DSN (e.g., `host:port/service`). |
| `DB_USER` / `DB_PASSWORD` | Oracle credentials (SYS requires SYSDBA). |
| `ORACLEVS_TABLE` | Default logical table (alias base name). |
| `OCI_REGION` | Human-readable region for logs. |
| `OCI_GENAI_ENDPOINT` | Base URL for OCI GenAI inference. |
| `OCI_COMPARTMENT_OCID` | Compartment containing models. |
| `OCI_AUTH_MODE` | `config_file` or alternative SDK mode. |
| `OCI_CONFIG_PATH` / `OCI_CONFIG_PROFILE` | Location/profile for OCI credentials. |
| `OCI_EMBED_MODEL_ID` | Embedding model alias or OCID. |
| `OCI_LLM_MODEL_ID` | Legacy single LLM ID (used when fallback not separated). |
| `OCI_LLM_PRIMARY_MODEL_ID`, `OCI_LLM_PRIMARY_ENDPOINT`, `OCI_LLM_PRIMARY_COMPARTMENT_OCID` | Primary chat model when split configs are used. |
| `OCI_LLM_FALLBACK_MODEL_ID`, `OCI_LLM_FALLBACK_ENDPOINT`, `OCI_LLM_FALLBACK_COMPARTMENT_OCID` | Fallback chat model. |

## Additional Environment Flags
- `APP_ENV_FILE`: override `.env` path search.
- `SANITIZE_*`: see [SANITIZATION.md](./SANITIZATION.md).
- `OCI_TESTS_DISABLED`: skip OCI adapter tests when set to `1`.

## TODO
- Clarify ownership of `retrieval.legacy` keys; they are currently unused in the runtime service.
- Consider consolidating duplicate LLM identifiers (`oci.models.chat` vs explicit `llm_primary`) to reduce confusion.
