"""
Voice-pool separate-only GPU worker (Cloud Run Job ``voice-pool-separate-job``).

Reads a JSONL manifest (written by karaoke-decide) of original-audio objects in
the gen bucket, separates the vocals stem with the ``instrumental_clean``
ensemble preset and writes ``voice-pool/stems/<id>/vocals.flac`` plus per-task
results. It touches no Firestore and no gen job state.

Manifest line:  {"id": pool_id, "audio": "gs://<bucket>/voice-pool/audio/<id>/original.flac"}
Results:        <manifest without .jsonl>.results/part-<task_index>.jsonl, one line per
                song: {"id", "ok", "vocals", "seconds", "error"}

Cloud Run shards the manifest across tasks (CLOUD_RUN_TASK_INDEX / CLOUD_RUN_TASK_COUNT).

Usage: python -m backend.workers.voice_pool_separate_worker --manifest gs://.../<run>.jsonl
"""
import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

INSTRUMENTAL_PRESET = "instrumental_clean"
STEMS_PREFIX = "voice-pool/stems"
RESULTS_FLUSH_EVERY = 10


def shard(lines: List[dict], index: int, count: int) -> List[dict]:
    """Lines owned by task ``index`` of ``count`` (round-robin over manifest order)."""
    return [line for i, line in enumerate(lines) if i % count == index]


def vocals_path_from_outputs(paths: List[str]) -> Optional[str]:
    """Pick the vocals stem from ensemble outputs.

    Filenames look like ``{base}_(Vocals)_preset_instrumental_clean.flac``; match the
    stem tag, never the preset name (which itself contains "instrumental").
    """
    for p in paths:
        if "_(vocals)_" in os.path.basename(p).lower():
            return p
    return None


def _blob_path(uri: str) -> str:
    """``gs://bucket/path`` -> ``path`` (StorageService is bound to the gen bucket)."""
    if uri.startswith("gs://"):
        return uri[5:].split("/", 1)[1]
    return uri


def _results_path(manifest_uri: str, index: int) -> str:
    base = _blob_path(manifest_uri)
    if base.endswith(".jsonl"):
        base = base[: -len(".jsonl")]
    return f"{base}.results/part-{index}.jsonl"


def _write_results(storage, manifest_uri: str, index: int, results: List[dict]) -> None:
    body = "".join(json.dumps(r) + "\n" for r in results)
    storage.upload_text(_results_path(manifest_uri, index), body, content_type="application/x-ndjson")


def _read_manifest(storage, manifest_uri: str) -> List[dict]:
    raw = storage.download_bytes(_blob_path(manifest_uri)).decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def run_manifest(
    storage,
    separator_factory: Callable[[], Any],
    manifest_uri: str,
    index: int,
    count: int,
) -> List[dict]:
    """Separate this task's share of the manifest; never aborts on a per-song failure."""
    lines = shard(_read_manifest(storage, manifest_uri), index, count)
    logger.info(f"Task {index}/{count}: {len(lines)} songs")
    results: List[dict] = []
    separator = None  # built lazily so an all-skipped shard never loads models

    for n, line in enumerate(lines, 1):
        pool_id = line.get("id")
        vocals_blob = f"{STEMS_PREFIX}/{pool_id}/vocals.flac"
        started = time.time()
        tmp = tempfile.mkdtemp(prefix="voicepool_")
        try:
            if storage.file_exists(vocals_blob):
                results.append({"id": pool_id, "ok": True, "vocals": vocals_blob, "seconds": 0, "error": None})
                continue
            if separator is None:
                separator = separator_factory()
            local = os.path.join(tmp, "original.flac")
            storage.download_file(_blob_path(line["audio"]), local)
            outputs = separator.separate(local)
            # Separator may return bare filenames relative to its output_dir
            out_dir = getattr(separator, "output_dir", None)
            outputs = [o if os.path.isabs(o) or not out_dir else os.path.join(out_dir, o) for o in outputs]
            vocals = vocals_path_from_outputs(outputs)
            if not vocals or not os.path.exists(vocals):
                raise RuntimeError(f"no vocals stem in separator outputs: {[os.path.basename(o) for o in outputs]}")
            storage.upload_file(vocals, vocals_blob)
            for o in outputs:  # all stems live in the shared output dir; free it per song
                try:
                    os.remove(o)
                except OSError:
                    pass
            results.append({
                "id": pool_id, "ok": True, "vocals": vocals_blob,
                "seconds": round(time.time() - started, 1), "error": None,
            })
        except Exception as e:  # per-song failure must not abort the task
            logger.warning(f"voice-pool separate failed for {pool_id}: {e}")
            results.append({
                "id": pool_id, "ok": False, "vocals": None,
                "seconds": round(time.time() - started, 1), "error": str(e)[:500],
            })
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            if n % RESULTS_FLUSH_EVERY == 0:
                _write_results(storage, manifest_uri, index, results)

    _write_results(storage, manifest_uri, index, results)
    return results


def make_separator_factory(output_dir: str) -> Callable[[], Any]:
    def factory():
        from audio_separator.separator import Separator

        from karaoke_gen.audio_processor import _separator_kwargs

        separator = Separator(
            model_file_dir=os.environ["MODEL_DIR"],
            output_format="FLAC",
            output_dir=output_dir,
            ensemble_preset=INSTRUMENTAL_PRESET,
            **_separator_kwargs(),
        )
        separator.load_model()
        return separator

    return factory


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="gs://bucket/voice-pool/manifests/<run>.jsonl")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    count = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))

    from backend.services.storage_service import StorageService

    out_dir = tempfile.mkdtemp(prefix="voicepool_out_")
    try:
        results = run_manifest(StorageService(), make_separator_factory(out_dir), args.manifest, index, count)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    failed = sum(1 for r in results if not r["ok"])
    logger.info(f"Task {index}/{count} done: {len(results) - failed} ok, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
