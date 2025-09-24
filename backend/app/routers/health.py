from fastapi import APIRouter

router = APIRouter()

@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "ai-assistant-backend", "version": "0.1.0"}
