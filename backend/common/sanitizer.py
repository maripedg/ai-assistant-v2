import os, re, json, logging, hashlib
from typing import Dict, Tuple, List
from collections import Counter
from pathlib import Path

# -----------------------
# Config vía entorno
# -----------------------
SAN_ENABLED = os.getenv("SANITIZE_ENABLED", "off").lower()    # off | shadow | on
SAN_PROFILE = os.getenv("SANITIZE_PROFILE", "default")
SAN_CFG_DIR = os.getenv("SANITIZE_CONFIG_PATH", "./config/sanitize")
SAN_MODE    = os.getenv("SANITIZE_PLACEHOLDER_MODE", "redact").lower()  # redact | pseudonym
SAN_SALT    = os.getenv("SANITIZE_HASH_SALT", "changeme")
SAN_AUDIT   = os.getenv("SANITIZE_AUDIT_ENABLED", "true").lower() == "true"

# -----------------------
# Logger de auditoría
# -----------------------
_san_logger = logging.getLogger("sanitizer")
if not _san_logger.handlers:
    _san_logger.setLevel(logging.INFO)
    fh = logging.FileHandler("sanitizer.log")
    fh.setFormatter(logging.Formatter("%(message)s"))
    _san_logger.addHandler(fh)

# -----------------------
# Utilidades
# -----------------------
_FLAG_MAP = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}

def _compile_pattern(pat: str, flags: str | None) -> re.Pattern:
    f = 0
    if flags:
        for ch in flags:
            f |= _FLAG_MAP.get(ch, 0)
    return re.compile(pat, f)

def _hash_token(value: str) -> str:
    return hashlib.sha256((SAN_SALT + value).encode("utf-8")).hexdigest()[:10]

def _placeholder(label: str, value: str, fmt_plain: str, fmt_pseudo: str) -> str:
    if SAN_MODE == "pseudonym":
        return fmt_pseudo.replace("{TYPE}", label.upper()).replace("{HASH}", _hash_token(value))
    return fmt_plain.replace("{TYPE}", label.upper())

def _luhn_ok(s: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", s)]
    if not digits:
        return False
    checksum, parity = 0, len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d = d * 2
            if d > 9: d -= 9
        checksum += d
    return checksum % 10 == 0

# -----------------------
# Carga de configuración
# -----------------------
class _SanitizeConfig:
    def __init__(self, cfg: Dict):
        self.cfg = cfg or {}
        ph = self.cfg.get("placeholder", {})
        self.ph_fmt = ph.get("format", "[{TYPE}]")
        self.ph_fmt_pseudo = ph.get("format_pseudonym", "[{TYPE}:{HASH}]")
        aw = self.cfg.get("allowlist", {})
        self.allow_tokens = set(aw.get("tokens", []) or [])
        # Compilar reglas
        self.rules = self._compile_rules(self.cfg.get("pii", {}))

    @staticmethod
    def _compile_rules(pii_cfg: Dict) -> List[Dict]:
        rules = []
        for label, spec in pii_cfg.items():
            if not spec or not spec.get("enabled", False):
                continue
            entry = {"label": label, "items": []}
            # una sola pattern o lista de patterns
            if "pattern" in spec:
                entry["items"].append({
                    "pattern": _compile_pattern(spec["pattern"], spec.get("flags")),
                    "group_value": spec.get("group_value"),
                    "validator": spec.get("validator")
                })
            for p in spec.get("patterns", []) or []:
                entry["items"].append({
                    "pattern": _compile_pattern(p["pattern"], p.get("flags")),
                    "group_value": p.get("group_value"),
                    "validator": p.get("validator")
                })
            if entry["items"]:
                rules.append(entry)
        return rules

_cfg_cache: Dict[str, _SanitizeConfig] = {}

def _load_config() -> _SanitizeConfig:
    key = f"{SAN_CFG_DIR}::{SAN_PROFILE}"
    if key in _cfg_cache:
        return _cfg_cache[key]
    path = Path(SAN_CFG_DIR) / f"{SAN_PROFILE}.patterns.json"
    if not path.exists():
        raise FileNotFoundError(f"Sanitize config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = _SanitizeConfig(data)
    _cfg_cache[key] = cfg
    return cfg

# -----------------------
# Núcleo de sanitización
# -----------------------
def _should_skip_by_allowlist(match_text: str, allow_tokens: set[str]) -> bool:
    # Muy simple: si el match contiene exactamente un token permitido o coincide totalmente con uno.
    mt = match_text.strip()
    for tok in allow_tokens:
        if tok in mt or mt == tok:
            return True
    return False

def _apply_rule(text: str, label: str, items: List[Dict], counters: Counter, cfg: _SanitizeConfig) -> str:
    # Para cada regex del label, hacemos sustitución con función para poder decidir group_value/validator/allowlist
    for it in items:
        pat: re.Pattern = it["pattern"]
        gidx = it.get("group_value", None)
        validator = it.get("validator")

        def _repl(m: re.Match) -> str:
            full = m.group(0)
            # allowlist
            if _should_skip_by_allowlist(full, cfg.allow_tokens):
                return full
            # valor que vamos a ocultar
            value = m.group(gidx) if gidx else full
            # validación opcional
            if validator == "luhn" and not _luhn_ok(value):
                return full
            # construye placeholder
            ph = _placeholder(label, value, cfg.ph_fmt, cfg.ph_fmt_pseudo)
            counters.update({label: 1})
            # Si hubo group_value, reconstruimos el match sustituyendo solo ese grupo
            if gidx:
                # reconstrucción: reemplaza el grupo capturado por el placeholder
                start, end = m.start(gidx) - m.start(0), m.end(gidx) - m.start(0)
                return full[:start] + ph + full[end:]
            return ph

        text = pat.sub(_repl, text)
    return text

def sanitize_if_enabled(text: str, doc_id: str) -> Tuple[str, Dict[str, int]]:
    """
    Devuelve (texto_sanitizado_o_original, contadores_por_tipo).
    - off: no toca el texto, counters vacíos.
    - shadow: no toca el texto, pero detecta y audita.
    - on: reemplaza por placeholders y audita.
    """
    mode = SAN_ENABLED
    if mode == "off":
        return text, {}

    cfg = _load_config()
    counters = Counter()

    # Ejecutar sustituciones sobre una copia para medir
    processed = text
    for rule in cfg.rules:
        processed = _apply_rule(processed, rule["label"], rule["items"], counters, cfg)

    if SAN_AUDIT and counters:
        _san_logger.info(json.dumps({
            "doc_id": doc_id,
            "profile": SAN_PROFILE,
            "mode": mode,
            "redactions": dict(counters)
        }))

    if mode == "shadow":
        return text, dict(counters)
    return processed, dict(counters)
