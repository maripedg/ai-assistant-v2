import logging

from fastapi import APIRouter

from app.deps import health_probe


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/healthz")
def healthz():
    services = {}
    probes = {
        "embeddings": health_probe("embeddings"),
        "llm_primary": health_probe("llm_primary"),
        "llm_fallback": health_probe("llm_fallback"),
    }

    for label, probe in probes.items():
        if probe["is_up"]:
            services[label] = "up"
        else:
            reason = probe.get("reason") or "error"
            services[label] = f"down ({reason})"
            logger.debug("Health detail for %s: %s", label, probe["info"])

    ok = all(probe["is_up"] for probe in probes.values())
    return {"ok": ok, "services": services}
