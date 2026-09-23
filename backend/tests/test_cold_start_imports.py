"""Guard the backend's cold-start import graph.

A freshly-spawned Cloud Run instance pays for every module imported by
``backend.main`` before it can serve its first request (Cloud Run holds routed
requests during boot). The 2026-09-22 cold-start work cut ~7.5s of import time
by making the heavy ML/audio libraries lazy; this test keeps them out of the
startup path so a stray top-level import can't silently reintroduce the
16-second cold start.

If this test fails: find the offending import chain with
``python -X importtime -c "import backend.main"`` and move the heavy import
into the function (or class __init__) that actually uses it.
"""

import json
import subprocess
import sys
from pathlib import Path

# Libraries that must NOT be imported by `import backend.main`. Each one is
# multi-hundred-ms to multi-second at import time and is only needed inside
# worker/background code paths, never to start serving HTTP.
FORBIDDEN_STARTUP_IMPORTS = [
    "audio_separator",  # ~2.9s; pulls torch/onnxruntime/librosa
    "torch",  # ~1.0-1.2s; pulled by audio_separator and spacy's thinc
    "spacy",  # ~1.3-1.7s; syllable_counter / phrase_analyzer lazy-load it
    "nltk",  # ~1.3s; syllable_counter lazy-loads it
    "matplotlib",  # instrumental_review waveform plotting only
    "pydub",  # audio manipulation in workers only
    "google.genai",  # ~0.6s; custom-lyrics / auto-correct call paths only
    "google.cloud.secretmanager",  # ~1.7s; config.get_secret lazy-loads it
    "librosa",  # pulled by audio_separator
    "onnxruntime",  # pulled by audio_separator
]

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_backend_main_does_not_import_heavy_modules():
    """`import backend.main` must leave all known-heavy libraries unimported.

    Runs in a subprocess because the pytest process itself imports several of
    these libraries via other tests' fixtures.
    """
    probe = (
        "import json, sys\n"
        "import backend.main\n"
        f"heavy = {FORBIDDEN_STARTUP_IMPORTS!r}\n"
        "print(json.dumps([m for m in heavy if m in sys.modules]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # The probe prints exactly one JSON line on stdout; tolerate stray log
    # lines from module-level logging by taking the last non-empty line.
    last_line = [line for line in result.stdout.strip().splitlines() if line.strip()][-1]
    leaked = json.loads(last_line)
    assert leaked == [], (
        f"Heavy modules leaked into the backend startup import graph: {leaked}. "
        "This regresses Cloud Run cold-start latency — make the import lazy "
        "(see this file's docstring)."
    )
