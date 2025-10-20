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
| `retrieval.hybrid` | `max_context_chars`, `max_chunks`, `min_tokens_per_chunk`, `min_similarity_for_hybrid`, `min_chunks_for_hybrid`, `min_total_context_chars` | Controls context assembly, evidence gate, and hybrid behavior. |
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
| `oci.llm_primary` | Primary chat model configuration; `model_id` may be alias or OCID. Optional generation params: `max_tokens`, `temperature`, `top_p`, `top_k`, `frequency_penalty`, `presence_penalty`. | `OCI_LLM_PRIMARY_MODEL_ID`, `OCI_LLM_PRIMARY_ENDPOINT`, `OCI_LLM_PRIMARY_COMPARTMENT_OCID`, `OCI_CONFIG_PATH`, `OCI_CONFIG_PROFILE` |
| `oci.llm_fallback` | Fallback chat model configuration (must be OCID). Optional generation params: `max_tokens`, `temperature`, `top_p`, `top_k`, `frequency_penalty`, `presence_penalty`. | `OCI_LLM_FALLBACK_MODEL_ID`, `OCI_LLM_FALLBACK_ENDPOINT`, `OCI_LLM_FALLBACK_COMPARTMENT_OCID`, `OCI_CONFIG_PATH`, `OCI_CONFIG_PROFILE` |
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
 
## Generation Parameters
- Scope: Only under `providers.oci.llm_primary` and `providers.oci.llm_fallback`.
- Supported keys: `max_tokens` (int > 0), `temperature` [0.0–2.0], `top_p` [0.0–1.0], `top_k` (int >= 0), `frequency_penalty` [0.0–2.0], `presence_penalty` [0.0–2.0].
- Behavior: All keys are optional. Missing keys preserve legacy behavior. Values outside ranges are clamped during startup and printed in the startup summary.
- Precedence: Values in the respective LLM section are applied to that LLM only; primary and fallback are independent.
 - Environment overrides: Each key may be overridden via environment variables with higher precedence than YAML defaults:
   - Primary: `OCI_LLM_PRIMARY_MAX_TOKENS`, `OCI_LLM_PRIMARY_TEMPERATURE`, `OCI_LLM_PRIMARY_TOP_P`, `OCI_LLM_PRIMARY_TOP_K`, `OCI_LLM_PRIMARY_FREQUENCY_PENALTY`, `OCI_LLM_PRIMARY_PRESENCE_PENALTY`.
   - Fallback: `OCI_LLM_FALLBACK_MAX_TOKENS`, `OCI_LLM_FALLBACK_TEMPERATURE`, `OCI_LLM_FALLBACK_TOP_P`, `OCI_LLM_FALLBACK_TOP_K`, `OCI_LLM_FALLBACK_FREQUENCY_PENALTY`, `OCI_LLM_FALLBACK_PRESENCE_PENALTY`.
### Hybrid Evidence Gate
- `min_similarity_for_hybrid` (float, default `0.0`): require at least this decision score (based on `score_mode`) to keep hybrid; otherwise fallback.
- `min_chunks_for_hybrid` (int, default `0`): require at least this many context chunks after dedupe/filters; otherwise fallback.
- `min_total_context_chars` (int, default `0`): require at least this many bytes of assembled context; otherwise fallback.
- When a gate forces fallback, `decision_explain.reason` is set to one of `gate_failed_min_similarity`, `gate_failed_min_chunks`, or `gate_failed_min_context`.
- If the primary LLM returns the exact `prompts.no_context_token`, the runtime falls back to the fallback LLM and sets `decision_explain.reason=llm_no_context_token`.
# Configuration Reference

## Purpose
Authoritative mapping of environment variables, YAML settings, and where they are used in code.

## Components / Architecture
- App config: `backend/config/app.yaml`
- Provider config: `backend/config/providers.yaml`
- Loader: `backend/app/deps.py` reads YAML, resolves env, and wires clients.

## Environment Variables (.env.example)

| VAR | Default/Example | Required? | Used by (module/path) | Notes |
| --- | --- | --- | --- | --- |
| DB_DSN | `10.0.0.3:1529/FREEPDB1` | Yes (for OracleVS) | `backend/config/providers.yaml` → `oraclevs.dsn` | Oracle 23ai DSN |
| DB_USER | `sys` | Yes | `backend/config/providers.yaml` → `oraclevs.user` | DB user; `SYS` requires proper auth mode |
| DB_PASSWORD | `YourSysPassword` | Yes | `backend/config/providers.yaml` → `oraclevs.password`; `backend/app/deps.py` (logs masked) | Keep secret |
| ORACLEVS_TABLE | `MY_DEMO` | Yes | `backend/config/providers.yaml` → `oraclevs.table` | Physical table base name |
| OCI_REGION | `us-chicago-1` | Yes | `backend/config/providers.yaml` → `oci.region` | Region must match endpoint |
| OCI_GENAI_ENDPOINT | `https://inference.generativeai.us-chicago-1.oci.oraclecloud.com` | Yes | `backend/config/providers.yaml` → `oci.endpoint`; embeddings/LLM sections | OCI GenAI Inference endpoint |
| OCI_COMPARTMENT_OCID | `ocid1.compartment.oc1..xxxxxxx` | Yes | `backend/config/providers.yaml` → `oci.compartment_id` | Target compartment for GenAI |
| OCI_AUTH_MODE | `config_file` | Yes | `backend/config/providers.yaml` → `oci.auth_mode` | Auth mode hint |
| OCI_CONFIG_PATH | `~/.oci/config` | Yes (for API key auth) | `backend/config/providers.yaml` → `oci.config_path`; used by adapters | Path to OCI config file |
| OCI_CONFIG_PROFILE | `DEFAULT` | Yes | `backend/config/providers.yaml` → `oci.config_profile` | Profile name in config |
| OCI_EMBED_MODEL_ID | `cohere.embed-english-v3.0` | Yes | `backend/config/providers.yaml` → `oci.models.embeddings` and `oci.embeddings` | Embedding model alias/OCID |
| OCI_LLM_MODEL_ID | `cohere.command-english-v3.0` | Yes (if used) | `backend/config/providers.yaml` (models.chat) | Generic chat alias (not primary/fallback) |

Additional LLM primary/fallback envs may be referenced in `providers.yaml` (e.g., `OCI_LLM_PRIMARY_*`, `OCI_LLM_FALLBACK_*`). If present in your environment, the loader maps them under `oci.llm_primary` and `oci.llm_fallback` in `deps.py`.

## YAML Parameters
- `backend/config/app.yaml`: retrieval thresholds, distance metric, embeddings profiles (active/legacy/standard), prompts.
- `backend/config/providers.yaml`: OCI endpoints/region/compartment/auth profile; OracleVS DSN/user/password/table; distance default.

## Examples
Load `.env` in development is automatic via `python-dotenv` in `backend/app/deps.py` (project root and `backend/`). In Docker, pass env via `--env-file` (see [Setup & Run](./SETUP_AND_RUN.md)).

```bash
# Local
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# Docker (example)
docker run --rm -p 8000:8000 --env-file backend/.env <image>
```

## Ops Notes
- Keep region and endpoint aligned.
- Ensure DB user/password and alias view are valid before serving traffic.
- For secrets, prefer environment or secret managers; do not commit real values.

## See also
- [Setup & Run](./SETUP_AND_RUN.md)
- [Embedding & Retrieval](./EMBEDDING_AND_RETRIEVAL.md)
