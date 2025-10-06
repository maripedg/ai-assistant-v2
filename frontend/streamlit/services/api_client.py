import json
import requests
from typing import Any, Dict, Tuple

class APIClient:
    def __init__(self, base_url: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health_check(self) -> Tuple[bool, Dict[str, Any]]:
        url = f"{self.base_url}/healthz"
        try:
            r = requests.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            ok = bool(data.get("ok", False))
            return ok, data
        except Exception as e:
            return False, {"ok": False, "error": str(e), "services": {}}

    def chat(self, question: str) -> Dict[str, Any]:
        """Devuelve siempre un dict normalizado con:
        answer, answer2, retrieved_chunks_metadata, raw, mode, used_chunks, decision_explain
        """
        url = f"{self.base_url}/chat"
        payload = {"question": question}
        out = {
            "answer": "",
            "answer2": None,
            "retrieved_chunks_metadata": [],
            "mode": None,
            "used_chunks": [],
            "decision_explain": {},
            "raw": None,
        }
        try:
            r = requests.post(url, json=payload, timeout=self.timeout, headers={"Content-Type": "application/json"})
            r.raise_for_status()
            # Backends pueden responder JSON directo con el objeto final:
            data = r.json()
            # Algunos backends anidan respuesta serializada en data["response"]:
            raw = data.get("response", data)
            out["raw"] = raw

            if isinstance(raw, str):
                # Intentar parsear string JSON:
                try:
                    parsed = json.loads(raw)
                    out.update({
                        "answer": parsed.get("answer", "") or "",
                        "answer2": parsed.get("answer2"),
                        "retrieved_chunks_metadata": parsed.get("retrieved_chunks_metadata", []) or [],
                        "mode": parsed.get("mode"),
                        "used_chunks": parsed.get("used_chunks", []) or [],
                        "decision_explain": parsed.get("decision_explain", {}) or {},
                    })
                except json.JSONDecodeError:
                    # Texto plano
                    out["answer"] = raw
            elif isinstance(raw, dict):
                out.update({
                    "answer": raw.get("answer", "") or "",
                    "answer2": raw.get("answer2"),
                    "retrieved_chunks_metadata": raw.get("retrieved_chunks_metadata", []) or [],
                    "mode": raw.get("mode"),
                    "used_chunks": raw.get("used_chunks", []) or [],
                    "decision_explain": raw.get("decision_explain", {}) or {},
                })
            else:
                # Formato inesperado: intenta mapear campos del data base
                out["answer"] = data.get("answer", "") or ""
                out["retrieved_chunks_metadata"] = data.get("retrieved_chunks_metadata", []) or []
            return out
        except Exception as e:
            return {**out, "answer": f"❌ Error contacting backend: {e}"}
