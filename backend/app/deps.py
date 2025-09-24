import logging
import os
import re
import yaml
from pathlib import Path
from typing import Optional, Any, Dict

import oci
from dotenv import load_dotenv
from langchain_community.embeddings import OCIGenAIEmbeddings
from providers.oci.vectorstore import OracleVSStore
from providers.oci.chat_model import OciChatModel
from providers.oci.chat_model_chat import OciChatModelChat


logger = logging.getLogger(__name__)

# ---------------- paths ----------------
BASE_DIR = Path(__file__).resolve().parents[1]  # backend/
REPO_ROOT = BASE_DIR.parent
CONFIG_DIR = BASE_DIR / "config"
ENV_PATH = BASE_DIR / ".env"
CFG_PATH = (REPO_ROOT / "oci" / "config").resolve()

# ---------------- env ----------------
load_dotenv(ENV_PATH)

# Forzar uso de config del repo (idéntico a test_embed)
os.environ["OCI_CONFIG_FILE"] = str(CFG_PATH)
os.environ["OCI_CONFIG_PROFILE"] = os.getenv("OCI_CONFIG_PROFILE", "DEFAULT")


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


class Settings:
    def __init__(self):
        self.app = _read_yaml(CONFIG_DIR / "app.yaml")
        self.providers = _deep_resolve_env(_read_yaml(CONFIG_DIR / "providers.yaml"))


settings = Settings()

_region_warning_cache: set[tuple[str, str, str]] = set()


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
    info = (
        f"endpoint={endpoint} | region={region} | profile={profile} | "
        f"model_id={model_id} ({model_kind})"
    )

    try:
        if section == "embeddings":
            client = OCIGenAIEmbeddings(
                model_id=cfg["model_id"],
                service_endpoint=cfg["endpoint"],
                compartment_id=cfg["compartment_id"],
                auth_file_location=cfg["auth_file"],
                auth_profile=cfg["auth_profile"],
            )
            client.embed_query("ping")
        elif section == "llm_primary":
            model_id = cfg["model_id"]
            if isinstance(model_id, str) and model_id.startswith("ocid1."):
                client = OciChatModelChat(
                    endpoint=cfg["endpoint"],
                    compartment_id=cfg["compartment_id"],
                    model_id=model_id,
                    auth_file_location=cfg["auth_file"],
                    auth_profile=cfg["auth_profile"],
                )
            else:
                client = OciChatModel(
                    model_id=model_id,
                    endpoint=cfg["endpoint"],
                    compartment_id=cfg["compartment_id"],
                    auth_file_location=cfg["auth_file"],
                    auth_profile=cfg["auth_profile"],
                )
            client.generate("ok")
        elif section == "llm_fallback":
            client = OciChatModelChat(
                endpoint=cfg["endpoint"],
                compartment_id=cfg["compartment_id"],
                model_id=cfg["model_id"],
                auth_file_location=cfg["auth_file"],
                auth_profile=cfg["auth_profile"],
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
    cfg = _load_oci_section("embeddings")
    return OCIGenAIEmbeddings(
        model_id=cfg["model_id"],
        service_endpoint=cfg["endpoint"],
        compartment_id=cfg["compartment_id"],
        auth_file_location=cfg["auth_file"],
        auth_profile=cfg["auth_profile"],
    )


def make_vector_store(embeddings):
    ovs = settings.providers["oraclevs"]
    return OracleVSStore(
        dsn=ovs["dsn"],
        user=ovs["user"],
        password=ovs["password"],
        table=ovs["table"],
        embeddings=embeddings,
        distance=ovs.get("distance", "dot_product"),
    )


def make_chat_model_primary():
    cfg = _load_oci_section("llm_primary")
    model_id = cfg["model_id"]
    if isinstance(model_id, str) and model_id.startswith("ocid1."):
        return OciChatModelChat(
            endpoint=cfg["endpoint"],
            compartment_id=cfg["compartment_id"],
            model_id=model_id,
            auth_file_location=cfg["auth_file"],
            auth_profile=cfg["auth_profile"],
        )
    return OciChatModel(
        model_id=model_id,
        endpoint=cfg["endpoint"],
        compartment_id=cfg["compartment_id"],
        auth_file_location=cfg["auth_file"],
        auth_profile=cfg["auth_profile"],
    )


def make_chat_model_fallback():
    cfg = _load_oci_section("llm_fallback")
    return OciChatModelChat(
        endpoint=cfg["endpoint"],
        compartment_id=cfg["compartment_id"],
        model_id=cfg["model_id"],
        auth_file_location=cfg["auth_file"],
        auth_profile=cfg["auth_profile"],
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


def health_probe(section: str) -> Dict[str, Any]:
    """Public helper for health checks."""
    return _probe_service(section)

