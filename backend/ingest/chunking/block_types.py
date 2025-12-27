from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Block:
    type: str
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)
