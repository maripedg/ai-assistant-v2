import logging
import os
import re
import time
from importlib import resources
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

import oci

from dotenv import load_dotenv

import oci
from backend.providers.oci.embeddings_adapter import OCIEmbeddingsAdapter

try:  # Optional at import time; enforced via requirements.
    from tenacity import before_sleep_log, retry, stop_after_delay, wait_exponential

    _TENACITY_AVAILABLE = True
except Exception:  # pragma: no cover - graceful degradation if missing
    _TENACITY_AVAILABLE = False


logger = logging.getLogger(__name__)
log = logging.getLogger("backend.app.deps")

_VECTOR_LOCK = Lock()
_VECTOR_CACHE: Dict[str, Any] = {"instance": None, "failures": 0, "opened_at": None}
_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_COOLDOWN_SECONDS = 30


class VectorInitError(RuntimeError):
    """Raised when the vector store cannot be initialized."""

# ---------------- paths ----------------
BASE_DIR = Path(__file__).resolve().parents[1]  # backend/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT
CFG_PATH = (PROJECT_ROOT / "oci" / "config").resolve()

# ---------------- env ----------------
_DOTENV_LOADED = False


def _load_env_file() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return

    candidates = []
    env_hint = os.environ.get("APP_ENV_FILE")
    if env_hint:
        candidates.append(Path(env_hint))
    candidates.append(PROJECT_ROOT / ".env")
    candidates.append(BASE_DIR / ".env")

    for candidate in candidates:
        candidate = Path(candidate).expanduser()
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        if candidate.exists():
            load_dotenv(candidate)
            break

    _DOTENV_LOADED = True
    db_password = os.getenv("DB_PASSWORD")
    if db_password is not None:
        preview = db_password if len(db_password) <= 6 else f"{db_password[:3]}***{db_password[-1]}"
        logger.info("Loaded DB_PASSWORD=%s", preview)


_load_env_file()

# Force using repo config (identical to test_embed)
os.environ.setdefault("OCI_CONFIG_FILE", str(CFG_PATH))
os.environ.setdefault("OCI_CONFIG_PROFILE", "DEFAULT")


# ---------------- settings ----------------
def _read_yaml(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _deep_resolve_env(obj):
    def resolve(v):
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            return os.getenv(v[2:-1])
        return v

    if isinstance(obj, dict):
        return {k: _deep_resolve_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve_env(v) for v in obj]
    return resolve(obj)


def _read_yaml_from_package(package: str, filename: str) -> Optional[Any]:
    try:
        resource = resources.files(package).joinpath(filename)
    except ModuleNotFoundError:
        return None

    if not resource.exists():
        return None

    with resources.as_file(resource) as resolved:
        path = Path(resolved)
        if not path.exists():
            return None
        return _read_yaml(path)


def _load_config_yaml(env_var: str, filename: str) -> Dict[str, Any]:
    override = os.environ.get(env_var)
    if override:
        override_path = Path(override).expanduser()
        if not override_path.is_absolute():
            override_path = (PROJECT_ROOT / override_path).resolve()
        if not override_path.exists():
            raise FileNotFoundError(f"Config file not found at {override_path}")
        return _read_yaml(override_path) or {}

    package_data = _read_yaml_from_package("backend.config", filename)
    if package_data is not None:
        return package_data or {}

    fallback_path = BASE_DIR / "config" / filename
    return _read_yaml(fallback_path) or {}


class Settings:
    def __init__(self):
        self.app = _load_config_yaml("APP_CONFIG_PATH", "app.yaml")
        self.providers = _deep_resolve_env(_load_config_yaml("PROVIDERS_CONFIG_PATH", "providers.yaml"))


settings = Settings()


def _get_embeddings_settings() -> Dict[str, Any]:
    embeddings_cfg = settings.app.get("embeddings", {}) or {}
    return embeddings_cfg if isinstance(embeddings_cfg, dict) else {}


def _resolve_alias_runtime_values() -> tuple[Optional[str], Optional[str], Optional[str]]:
    embeddings_cfg = _get_embeddings_settings()
    alias_cfg = embeddings_cfg.get("alias", {}) if isinstance(embeddings_cfg, dict) else {}
    if not isinstance(alias_cfg, dict):
        alias_cfg = {}
    active_profile = embeddings_cfg.get("active_profile")
    alias_name = alias_cfg.get("name")
    active_index = alias_cfg.get("active_index")
    return active_profile, alias_name, active_index


_startup_log_emitted = False
_region_warning_cache: set[tuple[str, str, str]] = set()


def _log_embedding_runtime_once() -> None:
    global _startup_log_emitted
    if _startup_log_emitted:
        return
    profile, alias, index = _resolve_alias_runtime_values()
    logger.info(
        "embedding_profile=%s alias=%s active_index=%s",
        profile or "<missing>",
        alias or "<missing>",
        index or "<missing>",
    )
    _startup_log_emitted = True


# ---------------- helpers ----------------
def _resolve_auth_file(raw_path: Optional[str]) -> str:
    if not raw_path:
        return str(CFG_PATH)
    path = Path(os.path.expanduser(raw_path))
    if not path.is_absolute():
        path = (REPO_ROOT / raw_path).resolve()
    return str(path)


def _load_oci_section(section: str) -> dict:
    oci_cfg = settings.providers.get("oci", {})
    data = dict(oci_cfg.get(section, {}))
    if not data:
        raise KeyError(f"Missing providers.oci.{section} configuration")

    data.setdefault("endpoint", oci_cfg.get("endpoint"))
    data.setdefault("compartment_id", oci_cfg.get("compartment_id"))
    data.setdefault("auth_file", oci_cfg.get("config_path"))
    data.setdefault(
        "auth_profile",
        oci_cfg.get("config_profile") or os.environ.get("OCI_CONFIG_PROFILE", "DEFAULT"),
    )

    required = ("endpoint", "compartment_id", "auth_file", "auth_profile")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"providers.oci.{section} missing required keys: {', '.join(missing)}")

    data["auth_file"] = _resolve_auth_file(data["auth_file"])
    _normalize_model_fields(section, data)
    # Attach validated generation parameters for chat sections
    if section in {"llm_primary", "llm_fallback"}:
        data["gen_params"] = _parse_generation_params(section, data)
    _warn_if_region_mismatch(section, data["endpoint"], data["auth_file"], data["auth_profile"])
    return data


def _normalize_model_fields(section: str, data: dict) -> None:
    if section == "embeddings":
        model_id = data.get("model_id")
        if not model_id:
            raise ValueError("providers.oci.embeddings requires a non-empty model_id")
        return

    if section not in {"llm_primary", "llm_fallback"}:
        return

    alias = data.get("model_id")
    ocid = data.get("model_ocid")

    if alias and ocid and alias != ocid:
        logger.warning(
            "providers.oci.%s provides both model_id (%s) and model_ocid (%s); ignoring model_ocid.",
            section,
            alias,
            ocid,
        )

    if not alias and ocid:
        data["model_id"] = ocid
    elif not alias and not ocid:
        raise ValueError(f"providers.oci.{section} must define either model_id or model_ocid")

    data.pop("model_ocid", None)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _parse_generation_params(section: str, data: dict) -> dict:
    """Extract and validate optional generation params from a provider section.

    Supported keys: max_tokens, temperature, top_p, top_k, frequency_penalty, presence_penalty
    Values outside acceptable ranges are clamped with a warning.
    """
    params_raw = {
        "max_tokens": data.get("max_tokens"),
        "temperature": data.get("temperature"),
        "top_p": data.get("top_p"),
        "top_k": data.get("top_k"),
        "frequency_penalty": data.get("frequency_penalty"),
        "presence_penalty": data.get("presence_penalty"),
    }

    # Environment overrides take precedence over YAML values
    sec = section.lower()
    if sec in {"llm_primary", "llm_fallback"}:
        prefix = "OCI_LLM_PRIMARY_" if sec == "llm_primary" else "OCI_LLM_FALLBACK_"
        env_map = {
            "max_tokens": os.getenv(prefix + "MAX_TOKENS"),
            "temperature": os.getenv(prefix + "TEMPERATURE"),
            "top_p": os.getenv(prefix + "TOP_P"),
            "top_k": os.getenv(prefix + "TOP_K"),
            "frequency_penalty": os.getenv(prefix + "FREQUENCY_PENALTY"),
            "presence_penalty": os.getenv(prefix + "PRESENCE_PENALTY"),
        }
        for k, v in env_map.items():
            if v is not None and v != "":
                params_raw[k] = v

    out: dict[str, int | float] = {}

    # max_tokens: positive int
    mt = params_raw.get("max_tokens")
    if mt is not None:
        try:
            mt_i = int(mt)
            if mt_i <= 0:
                logger.warning("generation.max_tokens <= 0; clamping to 1")
                mt_i = 1
            out["max_tokens"] = mt_i
        except Exception:
            logger.warning("generation.max_tokens is not an int; ignoring: %r", mt)

    # temperature: [0.0, 2.0]
    t = params_raw.get("temperature")
    if t is not None:
        try:
            tv = float(t)
            tv = _clamp(tv, 0.0, 2.0)
            out["temperature"] = tv
        except Exception:
            logger.warning("generation.temperature is not a float; ignoring: %r", t)

    # top_p: [0.0, 1.0]
    tp = params_raw.get("top_p")
    if tp is not None:
        try:
            tpv = float(tp)
            tpv = _clamp(tpv, 0.0, 1.0)
            out["top_p"] = tpv
        except Exception:
            logger.warning("generation.top_p is not a float; ignoring: %r", tp)

    # top_k: non-negative int
    tk = params_raw.get("top_k")
    if tk is not None:
        try:
            tkv = int(tk)
            if tkv < 0:
                logger.warning("generation.top_k < 0; clamping to 0")
                tkv = 0
            out["top_k"] = tkv
        except Exception:
            logger.warning("generation.top_k is not an int; ignoring: %r", tk)

    # frequency_penalty: [0.0, 2.0]
    fp = params_raw.get("frequency_penalty")
    if fp is not None:
        try:
            fpv = float(fp)
            fpv = _clamp(fpv, 0.0, 2.0)
            out["frequency_penalty"] = fpv
        except Exception:
            logger.warning("generation.frequency_penalty is not a float; ignoring: %r", fp)

    # presence_penalty: [0.0, 2.0]
    pp = params_raw.get("presence_penalty")
    if pp is not None:
        try:
            ppv = float(pp)
            ppv = _clamp(ppv, 0.0, 2.0)
            out["presence_penalty"] = ppv
        except Exception:
            logger.warning("generation.presence_penalty is not a float; ignoring: %r", pp)

    return out


def _format_gen_params(params: dict | None) -> str:
    p = params or {}
    keys = ("max_tokens", "temperature", "top_p", "top_k", "frequency_penalty", "presence_penalty")
    parts = [f"{k}={p.get(k, '-') if p.get(k) is not None else '-'}" for k in keys]
    return "{" + ", ".join(parts) + "}"


def _extract_region_from_endpoint(endpoint: str) -> Optional[str]:
    if not endpoint:
        return None
    match = re.search(r"\.([a-z0-9-]+)\.oci\.oraclecloud\.com", endpoint)
    if match:
        return match.group(1)
    return None


def _warn_if_region_mismatch(section: str, endpoint: str, auth_file: str, auth_profile: str) -> None:
    endpoint_region = _extract_region_from_endpoint(endpoint)
    config_region = None
    try:
        config = oci.config.from_file(file_location=auth_file, profile_name=auth_profile)
        config_region = config.get("region")
    except Exception as exc:  # noqa: BLE001 - logging only, do not block startup
        logger.debug(
            "Unable to load OCI config for section %s (profile=%s, file=%s): %s",
            section,
            auth_profile,
            auth_file,
            exc,
        )
        return

    if not endpoint_region or not config_region:
        return

    if endpoint_region == config_region:
        return

    cache_key = (section, endpoint_region, config_region)
    if cache_key in _region_warning_cache:
        return

    _region_warning_cache.add(cache_key)
    logger.warning(
        "OCI configuration for %s specifies region '%s' but endpoint '%s' targets region '%s'. "
        "Proceeding with the endpoint region; update your config profile if this is unintended.",
        section,
        config_region,
        endpoint,
        endpoint_region,
    )


def _summarize_exc(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status", None) if resp is not None else None
    if status is not None:
        return f"{type(exc).__name__} {status}"
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    first_line = message.splitlines()[0]
    if len(first_line) > 60:
        first_line = first_line[:57] + "..."
    return f"{type(exc).__name__}: {first_line}"


def _probe_service(section: str) -> Dict[str, Any]:
    try:
        cfg = _load_oci_section(section)
    except Exception as exc:  # noqa: BLE001
        reason = _summarize_exc(exc)
        return {
            "info": f"config error: {reason}",
            "is_up": False,
            "reason": reason,
        }

    endpoint = cfg.get("endpoint", "<missing>")
    profile = cfg.get("auth_profile", "<missing>")
    region = _extract_region_from_endpoint(endpoint) or "<unknown>"
    model_id = cfg.get("model_id", "<missing>")
    model_kind = "ocid" if isinstance(model_id, str) and model_id.startswith("ocid1.") else "alias"
    if model_id == "<missing>":
        model_kind = "?"
    gen_info = ""
    if section in ("llm_primary", "llm_fallback"):
        gen_info = f" | gen={_format_gen_params(cfg.get('gen_params'))}"
    info = (
        f"endpoint={endpoint} | region={region} | profile={profile} | "
        f"model_id={model_id} ({model_kind})" + gen_info
    )

    try:
        if section == "embeddings":
            # Test through the adapter (LangChain embeddings) to ensure compatibility
            emb_app = _get_embeddings_settings()
            input_types = (emb_app.get("profiles", {}) or {}).get(
                emb_app.get("active_profile"), {}
            ).get("input_types", {}) if isinstance(emb_app, dict) else {}
            doc_it = input_types.get("documents", "search_document")
            qry_it = input_types.get("queries", "search_query")
            adapter = OCIEmbeddingsAdapter(
                model_id=cfg["model_id"],
                service_endpoint=cfg["endpoint"],
                compartment_id=cfg["compartment_id"],
                auth_file_location=cfg["auth_file"],
                auth_profile=cfg["auth_profile"],
                doc_input_type=doc_it,
                query_input_type=qry_it,
            )
            adapter.embed_query("ping")
        elif section == "llm_primary":
            from backend.providers.oci.chat_model import OciChatModel
            from backend.providers.oci.chat_model_chat import OciChatModelChat

            model_id = cfg["model_id"]
            if isinstance(model_id, str) and model_id.startswith("ocid1."):
                client = OciChatModelChat(
                    endpoint=cfg["endpoint"],
                    compartment_id=cfg["compartment_id"],
                    model_id=model_id,
                    auth_file_location=cfg["auth_file"],
                    auth_profile=cfg["auth_profile"],
                    **(cfg.get("gen_params") or {}),
                )
            else:
                client = OciChatModel(
                    model_id=model_id,
                    endpoint=cfg["endpoint"],
                    compartment_id=cfg["compartment_id"],
                    auth_file_location=cfg["auth_file"],
                    auth_profile=cfg["auth_profile"],
                    **(cfg.get("gen_params") or {}),
                )
            client.generate("ok")
        elif section == "llm_fallback":
            from backend.providers.oci.chat_model_chat import OciChatModelChat

            client = OciChatModelChat(
                endpoint=cfg["endpoint"],
                compartment_id=cfg["compartment_id"],
                model_id=cfg["model_id"],
                auth_file_location=cfg["auth_file"],
                auth_profile=cfg["auth_profile"],
                **(cfg.get("gen_params") or {}),
            )
            client.generate("ok")
        else:
            raise ValueError(f"Unsupported section '{section}' for probing")
    except Exception as exc:  # noqa: BLE001
        reason = _summarize_exc(exc)
        return {
            "info": info,
            "is_up": False,
            "reason": reason,
        }

    return {"info": info, "is_up": True, "reason": None}


# ---------------- factories ----------------
def make_embeddings():
    _log_embedding_runtime_once()
    cfg = _load_oci_section("embeddings")
    emb_app = _get_embeddings_settings()  # lee de app.yaml
    input_types = (emb_app.get("profiles", {}) or {}).get(
        emb_app.get("active_profile"), {}
    ).get("input_types", {}) if isinstance(emb_app, dict) else {}
    doc_it = input_types.get("documents", "search_document")
    qry_it = input_types.get("queries", "search_query")

    # Devuelve el adapter (hereda de langchain_core.embeddings.Embeddings)
    return OCIEmbeddingsAdapter(
        model_id=cfg["model_id"],
        service_endpoint=cfg["endpoint"],
        compartment_id=cfg["compartment_id"],
        auth_file_location=cfg["auth_file"],
        auth_profile=cfg["auth_profile"],
        doc_input_type=doc_it,
        query_input_type=qry_it,
    )


def _resolve_alias_table() -> str:
    _, alias_name, _ = _resolve_alias_runtime_values()
    if not alias_name:
        raise ValueError("embeddings.alias.name must be configured")
    return alias_name


def make_vector_store(embeddings=None):
    _log_embedding_runtime_once()
    if embeddings is None:
        embeddings = make_embeddings()

    try:
        from backend.providers.oci.vectorstore import OracleVSStore
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "The 'oracledb' package is required for Oracle vector operations. "
            "Install it with `pip install oracledb`."
        ) from exc

    # Resolve alias (stable read surface)
    alias_name = _resolve_alias_table()
    ovs = settings.providers["oraclevs"]

    # Guard: ensure alias exists and is a VIEW before constructing the store
    try:
        import oracledb  # type: ignore
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "The 'oracledb' package is required for Oracle vector operations. "
            "Install it with `pip install oracledb`."
        ) from exc

    conn = oracledb.connect(user=ovs["user"], password=ovs["password"], dsn=ovs["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_type FROM user_objects WHERE object_name = :1",
                (alias_name.upper(),),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(
                    f"Alias view '{alias_name}' not found. Run the embedding job with --update-alias first."
                )
            obj_type = (row[0] or "").upper()
            if obj_type != "VIEW":
                raise RuntimeError(
                    f"Alias name '{alias_name}' is a {obj_type}. Drop/rename it; it must be a VIEW."
                )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    # Instantiate store pointing at the alias (read surface). Preventing auto-bootstrap is handled
    # by the guard above; OracleVS should not attempt to create a TABLE when a VIEW exists.
    return OracleVSStore(
        dsn=ovs["dsn"],
        user=ovs["user"],
        password=ovs["password"],
        table=alias_name,
        embeddings=embeddings,
        distance=ovs.get("distance", "dot_product"),
    )


def _create_vector_store(embeddings):
    """Helper to build the Oracle vector store (keeps compatibility with existing factory)."""
    return make_vector_store(embeddings)


def _init_vector_once(embeddings):
    """Single attempt to create the vector store; raises VectorInitError on failure."""
    try:
        store = _create_vector_store(embeddings)
        log.info("Oracle Vector Store initialized and cached.")
        return store
    except Exception as exc:  # noqa: BLE001 - need full stack
        log.exception("Oracle Vector Store init failed")
        raise VectorInitError(str(exc)) from exc


def _init_vector_with_retry(embeddings):
    if not _TENACITY_AVAILABLE:
        return _init_vector_once(embeddings)

    @retry(
        stop=stop_after_delay(20),
        wait=wait_exponential(multiplier=0.5, min=1, max=5),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def _inner():
        return _init_vector_once(embeddings)

    return _inner()


def _breaker_open(now: float) -> bool:
    with _VECTOR_LOCK:
        opened_at = _VECTOR_CACHE.get("opened_at")
        if opened_at is None:
            return False
        elapsed = now - opened_at
        if elapsed < _BREAKER_COOLDOWN_SECONDS:
            log.warning(
                "Oracle Vector Store circuit breaker open; %.0fs cooldown remaining",
                _BREAKER_COOLDOWN_SECONDS - elapsed,
            )
            return True

        # Cooldown elapsed: reset counters and allow a new attempt (half-open)
        _VECTOR_CACHE["opened_at"] = None
        _VECTOR_CACHE["failures"] = 0
        return False


def _on_vector_failure(now: float):
    with _VECTOR_LOCK:
        _VECTOR_CACHE["instance"] = None
        _VECTOR_CACHE["failures"] += 1
        failures = _VECTOR_CACHE["failures"]
        if failures >= _BREAKER_FAILURE_THRESHOLD and _VECTOR_CACHE.get("opened_at") is None:
            _VECTOR_CACHE["opened_at"] = now
            log.error(
                "Oracle Vector Store circuit breaker opened after %s consecutive failures",
                failures,
            )


def _on_vector_success(store):
    with _VECTOR_LOCK:
        previous_failures = _VECTOR_CACHE["failures"]
        _VECTOR_CACHE["instance"] = store
        _VECTOR_CACHE["failures"] = 0
        _VECTOR_CACHE["opened_at"] = None

    if previous_failures:
        log.info("Oracle Vector Store recovered after %s consecutive failures", previous_failures)


def get_vector_store_safe(embeddings: Optional[Any] = None):
    """
    Lazy singleton with retry + circuit breaker.
    Returns None when the store is currently unavailable.
    """
    if embeddings is None:
        embeddings = make_embeddings()

    with _VECTOR_LOCK:
        cached = _VECTOR_CACHE.get("instance")
        if cached is not None:
            return cached

    now = time.monotonic()
    if _breaker_open(now):
        return None

    try:
        store = _init_vector_with_retry(embeddings)
    except Exception:  # noqa: BLE001
        _on_vector_failure(now)
        return None

    _on_vector_success(store)
    return store


def make_chat_model_primary():
    from backend.providers.oci.chat_model import OciChatModel
    from backend.providers.oci.chat_model_chat import OciChatModelChat

    cfg = _load_oci_section("llm_primary")
    model_id = cfg["model_id"]
    if isinstance(model_id, str) and model_id.startswith("ocid1."):
        return OciChatModelChat(
            endpoint=cfg["endpoint"],
            compartment_id=cfg["compartment_id"],
            model_id=model_id,
            auth_file_location=cfg["auth_file"],
            auth_profile=cfg["auth_profile"],
            **(cfg.get("gen_params") or {}),
        )
    return OciChatModel(
        model_id=model_id,
        endpoint=cfg["endpoint"],
        compartment_id=cfg["compartment_id"],
        auth_file_location=cfg["auth_file"],
        auth_profile=cfg["auth_profile"],
        **(cfg.get("gen_params") or {}),
    )


def make_chat_model_fallback():
    from backend.providers.oci.chat_model_chat import OciChatModelChat

    cfg = _load_oci_section("llm_fallback")
    return OciChatModelChat(
        endpoint=cfg["endpoint"],
        compartment_id=cfg["compartment_id"],
        model_id=cfg["model_id"],
        auth_file_location=cfg["auth_file"],
        auth_profile=cfg["auth_profile"],
        **(cfg.get("gen_params") or {}),
    )


def make_chat_model():
    """Backward-compatible factory referencing the primary chat model."""
    return make_chat_model_primary()


# ---------------- opcional: self-test ----------------
def validate_startup(verbose: bool = True) -> None:
    if not verbose:
        return

    for label in ("embeddings", "llm_primary", "llm_fallback"):
        result = _probe_service(label)
        print(f'[{label}] {result["info"].strip()}')
        if result["is_up"]:
            print(f'[{label}] status=up')
        else:
            reason = result.get("reason") or "unknown"
            print(f'[{label}] status=down ({reason})')

    retrieval_cfg = settings.app.get("retrieval", {}) or {}

    def _safe_value(container: Any, key: str):
        if not isinstance(container, dict) or key not in container:
            return "(missing)"
        value = container.get(key)
        return "(missing)" if value is None else value

    top_k = _safe_value(retrieval_cfg, "top_k")
    thresholds = retrieval_cfg.get("thresholds") if isinstance(retrieval_cfg, dict) else None
    thr_low = _safe_value(thresholds or {}, "low")
    thr_high = _safe_value(thresholds or {}, "high")

    short_cfg = retrieval_cfg.get("short_query") if isinstance(retrieval_cfg, dict) else None
    short_max = _safe_value(short_cfg or {}, "max_tokens")
    short_low = _safe_value(short_cfg or {}, "threshold_low")
    short_high = _safe_value(short_cfg or {}, "threshold_high")

    expansions_cfg = retrieval_cfg.get("expansions") if isinstance(retrieval_cfg, dict) else None
    if isinstance(expansions_cfg, dict):
        exp_enabled = expansions_cfg.get("enabled")
        exp_terms = expansions_cfg.get("terms")
        exp_enabled = "(missing)" if exp_enabled is None else exp_enabled
        if isinstance(exp_terms, dict):
            exp_terms_count = len(exp_terms)
        else:
            exp_terms_count = "(missing)"
    else:
        exp_enabled = "(missing)"
        exp_terms_count = "(missing)"

    distance = _safe_value(retrieval_cfg, "distance")
    dedupe_by = _safe_value(retrieval_cfg, "dedupe_by")

    # Hybrid gate logging
    hybrid_cfg = retrieval_cfg.get("hybrid") if isinstance(retrieval_cfg, dict) else None
    if isinstance(hybrid_cfg, dict):
        gate_min_sim = hybrid_cfg.get("min_similarity_for_hybrid", 0.0)
        gate_min_chunks = hybrid_cfg.get("min_chunks_for_hybrid", 0)
        gate_min_ctx = hybrid_cfg.get("min_total_context_chars", 0)
    else:
        gate_min_sim, gate_min_chunks, gate_min_ctx = 0.0, 0, 0

    print(
        "retrieval: top_k={0}, threshold_low={1}, threshold_high={2}, short_query={{max_tokens:{3}, low:{4}, high:{5}}}, "
        "expansions_enabled={6}, expansion_terms={7}, distance={8}, dedupe_by={9}, "
        "hybrid_gates={{min_sim:{10}, min_chunks:{11}, min_ctx_chars:{12}}}".format(
            top_k,
            thr_low,
            thr_high,
            short_max,
            short_low,
            short_high,
            exp_enabled,
            exp_terms_count,
            distance,
            dedupe_by,
            gate_min_sim,
            gate_min_chunks,
            gate_min_ctx,
        )
    )

    prompts_cfg = settings.app.get("prompts", {}) or {}
    rag_cfg = prompts_cfg.get("rag", {}) if isinstance(prompts_cfg, dict) else None
    fallback_cfg = prompts_cfg.get("fallback", {}) if isinstance(prompts_cfg, dict) else None
    no_ctx_token = prompts_cfg.get("no_context_token") if isinstance(prompts_cfg, dict) else None
    rag_style = rag_cfg.get("style") if isinstance(rag_cfg, dict) else None
    rag_max = rag_cfg.get("max_output_tokens") if isinstance(rag_cfg, dict) else None
    fallback_max = fallback_cfg.get("max_output_tokens") if isinstance(fallback_cfg, dict) else None
    print(
        "prompts: no_context_token={0}, rag_style={1}, rag_max_tokens={2}, fallback_max_tokens={3}".format(
            no_ctx_token or "(missing)",
            rag_style or "(missing)",
            rag_max if rag_max is not None else "(missing)",
            fallback_max if fallback_max is not None else "(missing)",
        )
    )


def health_probe(section: str) -> Dict[str, Any]:
    """Public helper for health checks."""
    return _probe_service(section)
