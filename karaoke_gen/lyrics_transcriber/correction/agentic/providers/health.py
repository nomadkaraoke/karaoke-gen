from typing import List, Dict, Any


def is_ollama_available() -> bool:
    """Return True if a local Ollama server responds to a simple list() call.

    This function is intentionally lightweight and safe to call during setup.
    """
    try:
        import ollama  # type: ignore

        _ = ollama.list()
        return True
    except Exception:
        return False


def get_ollama_models() -> List[Dict[str, Any]]:
    """Return available local models from Ollama if available; otherwise empty list."""
    try:
        import ollama  # type: ignore

        data = ollama.list() or {}
        return data.get("models", []) if isinstance(data, dict) else []
    except Exception:
        return []


