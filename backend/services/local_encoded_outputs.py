"""Same-box handoff of encoded finals from the encoding worker to the video worker.

On the AWS encoding worker, the video worker (distribution) runs on the same box
as the encoding service. Without this, the video worker would download every
final the encoder just uploaded back out of GCS — ~400 MB of out-of-pocket GCS
egress per job, more than moving distribution to AWS saves. So the encoder
"stashes" a local copy of each output keyed by its GCS blob path, and the video
worker "takes" it instead of downloading.

Active only when ``LOCAL_ENCODED_OUTPUTS_DIR`` is set (the AWS box). Everywhere
else ``stash`` is a no-op and ``take`` returns False, i.e. today's behaviour.
Best effort: any failure falls back to GCS.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ENV_VAR = "LOCAL_ENCODED_OUTPUTS_DIR"
MAX_AGE_SECONDS = 24 * 3600


def _root() -> Optional[Path]:
    raw = os.environ.get(ENV_VAR, "").strip()
    return Path(raw) if raw else None


def _path_for(root: Path, blob_path: str) -> Path:
    rel = blob_path.lstrip("/")
    target = (root / rel).resolve()
    if root.resolve() not in target.parents:
        raise ValueError(f"blob path escapes the stash dir: {blob_path!r}")
    return target


def stash(local_file: Path, blob_path: str) -> bool:
    """Keep a copy of ``local_file`` for a co-located video worker. Never raises."""
    root = _root()
    if root is None:
        return False
    try:
        dest = _path_for(root, blob_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".partial")
        shutil.copyfile(local_file, tmp)
        os.replace(tmp, dest)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not stash %s for local handoff: %s", blob_path, e)
        return False


def take(blob_path: str, dest: str) -> bool:
    """Move the stashed copy of ``blob_path`` to ``dest``. False ⇒ download from GCS."""
    root = _root()
    if root is None:
        return False
    try:
        src = _path_for(root, blob_path)
        if not src.is_file():
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Local handoff of %s failed, falling back to GCS: %s", blob_path, e)
        return False


def purge_stale(max_age_seconds: float = MAX_AGE_SECONDS) -> int:
    """Delete stashed files older than ``max_age_seconds`` (never taken). Returns count."""
    root = _root()
    if root is None or not root.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed
