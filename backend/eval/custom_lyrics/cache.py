"""Disk-backed cache for LLM responses, keyed by (system, user, model, settings)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


class LlmCallCache:
    def __init__(self, cache_dir: Path, replay_only: bool = False) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.replay_only = replay_only

    def _key(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        settings_dict: dict[str, Any],
    ) -> str:
        h = hashlib.sha256()
        h.update(system_prompt.encode())
        h.update(b"\x00")
        h.update(user_prompt.encode())
        h.update(b"\x00")
        h.update(model.encode())
        h.update(b"\x00")
        h.update(json.dumps(settings_dict, sort_keys=True).encode())
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        settings_dict: dict[str, Any],
    ) -> Optional[list[str]]:
        path = self._path(self._key(system_prompt, user_prompt, model=model, settings_dict=settings_dict))
        if not path.exists():
            if self.replay_only:
                raise RuntimeError(f"cache miss in replay-only mode: {path.name}")
            return None
        return json.loads(path.read_text())["lines"]

    def set(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        settings_dict: dict[str, Any],
        response_lines: list[str],
    ) -> None:
        if self.replay_only:
            return
        path = self._path(self._key(system_prompt, user_prompt, model=model, settings_dict=settings_dict))
        path.write_text(json.dumps({"lines": response_lines}, indent=2))
