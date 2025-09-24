# backend/app/deps.py
import os, yaml
from pathlib import Path
from dotenv import load_dotenv
import oci
from langchain_community.embeddings import OCIGenAIEmbeddings
from providers.oci.vectorstore import OracleVSStore
from providers.oci.chat_model import OciChatModel

# ---------------- paths ----------------
BASE_DIR   = Path(__file__).resolve().parents[1]   # backend/
REPO_ROOT  = BASE_DIR.parent
CONFIG_DIR = BASE_DIR / "config"
ENV_PATH   = BASE_DIR / ".env"
CFG_PATH   = (REPO_ROOT / "oci" / "config").resolve()

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
        self.app       = _read_yaml(CONFIG_DIR / "app.yaml")
        self.providers = _deep_resolve_env(_read_yaml(CONFIG_DIR / "providers.yaml"))

settings = Settings()

# ---------------- factories ----------------
def make_embeddings():
    MODEL       = settings.providers["oci"]["models"]["embeddings"]
    ENDPOINT    = settings.providers["oci"]["endpoint"]
    COMPARTMENT = settings.providers["oci"]["compartment_id"]

    return OCIGenAIEmbeddings(
        model_id=MODEL,
        service_endpoint=ENDPOINT,
        compartment_id=COMPARTMENT,
        auth_file_location=str(CFG_PATH),
        auth_profile=os.environ["OCI_CONFIG_PROFILE"],
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

def make_chat_model():
    oci_cfg = settings.providers["oci"]
    return OciChatModel(
        model_id=oci_cfg["models"]["chat"],
        endpoint=oci_cfg["endpoint"],
        compartment_id=oci_cfg["compartment_id"],
        # igual que embeddings: ya toma auth del CFG_PATH / PROFILE
    )

# ---------------- opcional: self-test ----------------
def validate_startup(verbose: bool = True):
    if verbose:
        print("OCI_CONFIG_FILE   =", os.environ.get("OCI_CONFIG_FILE"))
        print("OCI_CONFIG_PROFILE=", os.environ.get("OCI_CONFIG_PROFILE"))

    emb = make_embeddings()
    v = emb.embed_query("ping")
    if verbose: print("Embeddings OK | dimensión =", len(v))
    return True
