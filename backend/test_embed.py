import os
from pathlib import Path
from dotenv import load_dotenv
import oci
from langchain_community.embeddings import OCIGenAIEmbeddings

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
CFG_PATH = (REPO_ROOT / "oci" / "config").resolve()

# 1. Cargar .env
load_dotenv(BASE_DIR / ".env")

# 2. Forzar al SDK de OCI a usar tu config local
os.environ["OCI_CONFIG_FILE"] = str(CFG_PATH)
os.environ["OCI_CONFIG_PROFILE"] = os.getenv("OCI_CONFIG_PROFILE", "DEFAULT")

print("=== VARIABLES DE ENTORNO (.env) ===")
print("OCI_GENAI_ENDPOINT   =", os.getenv("OCI_GENAI_ENDPOINT"))
print("OCI_EMBED_MODEL_ID   =", os.getenv("OCI_EMBED_MODEL_ID"))
print("OCI_LLM_MODEL_ID     =", os.getenv("OCI_LLM_MODEL_ID"))
print("OCI_COMPARTMENT_OCID =", os.getenv("OCI_COMPARTMENT_OCID"))
print("OCI_REGION           =", os.getenv("OCI_REGION"))

print("\n=== CONFIG OCI (desde archivo) ===")
print("OCI_CONFIG_FILE      =", os.environ["OCI_CONFIG_FILE"])
print("OCI_CONFIG_PROFILE   =", os.environ["OCI_CONFIG_PROFILE"])
cfg = oci.config.from_file(os.environ["OCI_CONFIG_FILE"], os.environ["OCI_CONFIG_PROFILE"])
for k, v in cfg.items():
    if k == "key_file":
        print(f"{k} = {v}  (exists={os.path.exists(v)})")
    else:
        print(f"{k} = {v}")

print("\n=== CREANDO CLIENTE EMBEDDINGS ===")
MODEL = os.getenv("OCI_EMBED_MODEL_ID")
ENDPOINT = os.getenv("OCI_GENAI_ENDPOINT")
COMPARTMENT = os.getenv("OCI_COMPARTMENT_OCID")

# IMPORTANT: langchain's OCIGenAIEmbeddings does NOT read OCI_CONFIG_FILE/PROFILE
# from environment; we must pass them explicitly via auth_file_location/auth_profile.
emb = OCIGenAIEmbeddings(
    model_id=MODEL,
    service_endpoint=ENDPOINT,
    compartment_id=COMPARTMENT,
    auth_file_location=str(CFG_PATH),
    auth_profile=os.environ["OCI_CONFIG_PROFILE"],
)

print("Invocando embed_query('ping')...")
vec = emb.embed_query("ping")
print("OK | dimensión =", len(vec))
