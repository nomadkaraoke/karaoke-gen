# Backend cold-start reduction: ~16s → target <5s (2026-09-22)

Follow-up to the concurrent-review-reliability initiative. A freshly-spawned
`karaoke-backend` Cloud Run instance took **~16s before serving its first
request** (Cloud Run holds routed requests during boot), so every scale-out
event was a 16s latency cliff. Handoff spec:
`nomadkaraoke/docs/archive/2026-09-22-gen-cold-start-reduction-handoff.md`.

## Measured breakdown (before)

| Phase | Time | Evidence |
|-------|------|----------|
| Python imports (`import backend.main`) | ~8–9s prod / 10.63s local | `python -X importtime` |
| Lifespan preloads (blocked readiness) | ~7.5s | prod logs: "Starting karaoke generation backend" → "Application startup complete" (spaCy ~1.0s, cmudict ~1.4s, punkt ~0.9s, Langfuse ~2.5–3.3s, credential validation ~1.6s) |

Import graph offenders (cumulative, local):
- 6.1s `routes/internal` → workers → `karaoke_gen.lyrics_processor` (spacy 1.3s
  + nltk 1.3s via `syllable_counter`; spacy AGAIN via `phrase_analyzer`, which
  also pulls **torch 1.0s** through thinc) and `karaoke_gen.audio_processor` →
  `audio_separator` (torch, onnxruntime, librosa — 2.9s)
- 1.7s `backend.config` → `google.cloud.secretmanager` (google.api_core/grpc)
- 1.2s `google.genai` ×2 (custom_lyrics service + auto_correct service)

## Changes

**Lazy imports at the source** (fixes every importer at once, keeps CLI
behavior — the import just happens at first use inside worker code):
- `karaoke_gen/lyrics_transcriber/utils/syllable_counter.py` — spacy/nltk/
  pyphen/syllables into `__init__`/methods
- `karaoke_gen/lyrics_transcriber/correction/phrase_analyzer.py` — spacy into
  `__init__`; `Doc` under `TYPE_CHECKING`
- `karaoke_gen/audio_processor.py` — audio_separator via PEP 562 module
  `__getattr__` so tests can keep patching `audio_processor.Separator` /
  `REMOTE_API_AVAILABLE` etc. **Intra-module code must use the `_separator_cls()`
  / `_remote_api_available()` helpers** — LOAD_GLOBAL inside the module does
  NOT trigger module `__getattr__`.
- `karaoke_gen/lyrics_transcriber/transcribers/{audioshake,whisper}.py`,
  `output/cdgmaker/composer.py` — pydub into the methods that use it
- `backend/config.py` — secretmanager into `get_secret()`
- `backend/services/custom_lyrics/service.py` — google.genai into
  `_call_gemini()`; module `__getattr__` keeps `service.genai` patchable for
  tests
- `backend/services/auto_correct/service.py` — google.genai into `_call_gemini()`

**Lifespan** (`backend/main.py`): the spaCy/NLTK/Langfuse preloads +
credential validation moved from inline lifespan startup into a daemon thread
(`_run_background_warmup`). They exist to warm caches for the FIRST
lyrics-processing job — they never needed to gate HTTP readiness. Workers that
race the warmup fall back to the preloaders' existing lazy paths.

**Result:** local `import backend.main` 10.63s → **2.75s**; lifespan startup
~7.5s → instant. Remaining import cost is dominated by
`google.cloud.firestore` (~0.8s) which is a genuine serving dependency.

**Guard:** `backend/tests/test_cold_start_imports.py` — subprocess-imports
`backend.main` and asserts none of the heavy libraries land in `sys.modules`.
`backend/tests/test_main_startup_warmup.py` pins the background-warmup wiring.

## Follow-up (after prod verification)

If fresh-instance first-request latency is <5s in prod, drop
`--min-instances 2 → 1` in `.github/workflows/ci.yml` (the second warm
instance was the mitigation, ~$15/mo).
