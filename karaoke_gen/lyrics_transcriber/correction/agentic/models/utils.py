from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict


def to_serializable_dict(obj: Any) -> Dict[str, Any]:
    """Serialize dataclass or dict-like object to a plain dict for JSON.

    This avoids pulling in runtime deps for Pydantic here; enforcement occurs in
    workflow layers using Instructor/pydantic-ai as per guidance.
    """
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Unsupported object type for serialization: {type(obj)!r}")


