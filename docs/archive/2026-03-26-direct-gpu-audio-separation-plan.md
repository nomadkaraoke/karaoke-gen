# Direct GPU Audio Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the `Separator` class directly in the audio worker Cloud Run Job with an L4 GPU, eliminating the HTTP call to the separate audio-separator service.

**Architecture:** Build a GPU-capable Docker base image (`karaoke-backend-gpu`) with CUDA, PyTorch, and baked-in models. Modify the audio worker Cloud Run Job infra to use this image with a GPU in us-east4. Remove the `AUDIO_SEPARATOR_API_URL` requirement so the existing local `Separator` code path runs instead of the remote API path. The old separator service stays for rollback.

**Tech Stack:** Python 3.11, NVIDIA CUDA 12.6, PyTorch, ONNX Runtime GPU, audio-separator package, Pulumi IaC, Cloud Build, Cloud Run Jobs

**Spec:** `docs/archive/2026-03-26-direct-gpu-audio-separation-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/Dockerfile.gpu-base` | GPU base image: CUDA + PyTorch + models + all karaoke-gen deps |
| Create | `backend/cloudbuild-gpu-base.yaml` | Cloud Build config for GPU base image |
| Create | `backend/scripts/download_models.py` | Downloads ensemble preset models at image build time |
| Modify | `backend/workers/audio_worker.py:201-256` | `create_audio_processor()` — accept `model_file_dir`, pass through |
| Modify | `backend/workers/audio_worker.py:329-335` | Remove hard error when `AUDIO_SEPARATOR_API_URL` is absent |
| Modify | `karaoke_gen/audio_processor.py:168-200` | Local path: use `ensemble_preset` on `Separator` constructor |
| Modify | `infrastructure/modules/cloud_run.py:163-252` | GPU + us-east4 for audio worker job |
| Create | `infrastructure/modules/gpu_artifact_registry.py` | Artifact Registry repo in us-east4 for GPU images |
| Modify | `infrastructure/__main__.py:148` | Wire in GPU artifact registry |
| Create | `tests/unit/test_audio_processor_local_gpu.py` | Tests for local GPU separation code path |
| Modify | `tests/unit/test_audio_remote.py` | Update existing tests for new `create_audio_processor` signature |

---

## Task 1: Add `model_file_dir` parameter to `create_audio_processor()`

**Files:**
- Create: `tests/unit/test_audio_processor_local_gpu.py`
- Modify: `backend/workers/audio_worker.py:201-256`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_audio_processor_local_gpu.py`:

```python
import os
import logging
import tempfile
from unittest.mock import patch

from backend.workers.audio_worker import create_audio_processor


class TestCreateAudioProcessorLocalGPU:
    """Tests for create_audio_processor with local GPU (model_file_dir set)."""

    def test_model_file_dir_passed_through(self):
        """When model_file_dir is provided, AudioProcessor gets it for local separation."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
            model_file_dir="/models",
        )
        assert processor.model_file_dir == "/models"

    def test_model_file_dir_none_by_default(self):
        """When model_file_dir is not provided, AudioProcessor gets None (remote mode)."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
        )
        assert processor.model_file_dir is None

    def test_presets_set_by_default(self):
        """Ensemble presets are always set on the processor."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
            model_file_dir="/models",
        )
        assert processor.instrumental_preset == "instrumental_clean"
        assert processor.karaoke_preset == "karaoke"

    def test_custom_presets_override_defaults(self):
        """Custom presets override the defaults."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
            model_file_dir="/models",
            instrumental_preset="instrumental_full",
            karaoke_preset="vocal_clean",
        )
        assert processor.instrumental_preset == "instrumental_full"
        assert processor.karaoke_preset == "vocal_clean"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_processor_local_gpu.py -v 2>&1 | tail -20`
Expected: FAIL — `create_audio_processor() got an unexpected keyword argument 'model_file_dir'`

- [ ] **Step 3: Add `model_file_dir` parameter to `create_audio_processor()`**

In `backend/workers/audio_worker.py`, modify the function signature and body:

```python
def create_audio_processor(
    temp_dir: str,
    clean_instrumental_model: Optional[str] = None,
    backing_vocals_models: Optional[list] = None,
    other_stems_models: Optional[list] = None,
    instrumental_preset: Optional[str] = None,
    karaoke_preset: Optional[str] = None,
    model_file_dir: Optional[str] = None,
) -> AudioProcessor:
    """
    Create an AudioProcessor instance for audio separation.

    Supports two modes:
    - Local GPU mode: When model_file_dir is set, uses the Separator class directly
      with models from the specified directory. No remote API needed.
    - Remote API mode: When model_file_dir is None, requires AUDIO_SEPARATOR_API_URL
      env var to call the remote audio-separator service.

    Preset mode (default) uses ensemble presets for higher quality separation.
    Model mode (legacy) uses explicit model filenames for per-job overrides.

    Args:
        temp_dir: Temporary directory for processing
        clean_instrumental_model: Model for clean instrumental (legacy, overrides preset)
        backing_vocals_models: Models for backing vocals (legacy, overrides preset)
        other_stems_models: Models for other stems (legacy, default empty)
        instrumental_preset: Ensemble preset for instrumental separation
        karaoke_preset: Ensemble preset for karaoke/BV separation
        model_file_dir: Path to baked-in models directory (enables local GPU mode)

    Returns:
        Configured AudioProcessor instance
    """
    audio_logger = logging.getLogger("karaoke_gen.audio_processor")
    audio_logger.setLevel(logging.INFO)

    # Determine effective configuration
    effective_clean_model = clean_instrumental_model or DEFAULT_CLEAN_MODEL
    effective_backing_models = backing_vocals_models or DEFAULT_BACKING_MODELS
    effective_other_models = other_stems_models or DEFAULT_OTHER_MODELS

    ffmpeg_base_command = "ffmpeg -hide_banner -loglevel error -nostats -y"

    processor = AudioProcessor(
        logger=audio_logger,
        log_level=logging.INFO,
        log_formatter=None,
        model_file_dir=model_file_dir,
        lossless_output_format="FLAC",
        clean_instrumental_model=effective_clean_model,
        backing_vocals_models=effective_backing_models,
        other_stems_models=effective_other_models,
        ffmpeg_base_command=ffmpeg_base_command,
    )

    # Set preset configuration (used by both remote and local paths)
    processor.instrumental_preset = instrumental_preset or DEFAULT_INSTRUMENTAL_PRESET
    processor.karaoke_preset = karaoke_preset or DEFAULT_KARAOKE_PRESET

    return processor
```

The only change from the existing code is:
1. Added `model_file_dir: Optional[str] = None` parameter
2. Pass `model_file_dir` instead of hardcoded `None` to `AudioProcessor()`
3. Updated docstring

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_processor_local_gpu.py -v 2>&1 | tail -20`
Expected: All 4 tests PASS

- [ ] **Step 5: Run existing audio tests to verify no regressions**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_remote.py -v 2>&1 | tail -20`
Expected: All existing tests PASS (the new parameter has a default value of `None`, matching previous behavior)

- [ ] **Step 6: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu
git add tests/unit/test_audio_processor_local_gpu.py backend/workers/audio_worker.py
git commit -m "feat: add model_file_dir parameter to create_audio_processor"
```

---

## Task 2: Remove hard error when `AUDIO_SEPARATOR_API_URL` is absent

**Files:**
- Modify: `backend/workers/audio_worker.py:329-335`
- Modify: `tests/unit/test_audio_processor_local_gpu.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_audio_processor_local_gpu.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from backend.workers.audio_worker import process_audio_separation


class TestProcessAudioSeparationLocalGPU:
    """Tests for process_audio_separation without AUDIO_SEPARATOR_API_URL."""

    @pytest.mark.asyncio
    @patch("backend.workers.audio_worker.worker_registry")
    @patch("backend.workers.audio_worker.setup_job_logging")
    @patch("backend.workers.audio_worker.create_job_logger")
    @patch("backend.workers.audio_worker.JobManager")
    @patch("backend.workers.audio_worker.StorageService")
    @patch("backend.workers.audio_worker.get_settings")
    @patch("backend.workers.audio_worker.validate_worker_can_run", return_value=None)
    @patch("backend.workers.audio_worker.download_audio")
    @patch("backend.workers.audio_worker._store_audio_source_metadata")
    @patch("backend.workers.audio_worker.create_audio_processor")
    @patch("backend.workers.audio_worker.upload_separation_results", new_callable=AsyncMock)
    @patch.dict(os.environ, {}, clear=False)
    async def test_no_api_url_uses_local_gpu_when_model_dir_set(
        self,
        mock_upload,
        mock_create_processor,
        mock_store_metadata,
        mock_download,
        mock_validate,
        mock_settings,
        mock_storage,
        mock_job_manager_cls,
        mock_create_logger,
        mock_setup_logging,
        mock_registry,
    ):
        """When AUDIO_SEPARATOR_API_URL is not set but MODEL_DIR is, use local GPU."""
        # Remove AUDIO_SEPARATOR_API_URL if present
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)
        os.environ["MODEL_DIR"] = "/models"

        # Set up mocks
        mock_job = MagicMock()
        mock_job.artist = "Test"
        mock_job.title = "Song"
        mock_job.url = None
        mock_job.input_media_gcs_path = "audio/test.wav"
        mock_job.clean_instrumental_model = None
        mock_job.backing_vocals_models = None
        mock_job.other_stems_models = None

        mock_jm = MagicMock()
        mock_jm.get_job.return_value = mock_job
        mock_job_manager_cls.return_value = mock_jm

        mock_settings_obj = MagicMock()
        mock_settings_obj.gcs_bucket_name = "test-bucket"
        mock_settings.return_value = mock_settings_obj

        mock_download.return_value = "/tmp/test.wav"
        mock_create_logger.return_value = MagicMock()

        mock_processor = MagicMock()
        mock_processor.process_audio_separation.return_value = {
            "clean_instrumental": {"vocals": "/tmp/v.flac", "instrumental": "/tmp/i.flac"},
            "other_stems": {},
            "backing_vocals": {},
            "combined_instrumentals": {},
        }
        mock_create_processor.return_value = mock_processor

        result = await process_audio_separation("test-job-id")

        assert result is True
        # Verify create_audio_processor was called with model_file_dir="/models"
        call_kwargs = mock_create_processor.call_args
        assert call_kwargs[1].get("model_file_dir") == "/models" or \
               (len(call_kwargs[0]) > 1 and call_kwargs[0][0] is not None)

        # Clean up
        os.environ.pop("MODEL_DIR", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_processor_local_gpu.py::TestProcessAudioSeparationLocalGPU -v 2>&1 | tail -20`
Expected: FAIL — the current code raises `Exception("AUDIO_SEPARATOR_API_URL environment variable not set...")`

- [ ] **Step 3: Modify `process_audio_separation` to support local GPU mode**

In `backend/workers/audio_worker.py`, replace lines 329-335:

**Old code:**
```python
                # Ensure AUDIO_SEPARATOR_API_URL is set
                api_url = os.environ.get("AUDIO_SEPARATOR_API_URL")
                if not api_url:
                    raise Exception("AUDIO_SEPARATOR_API_URL environment variable not set. "
                                  "Cannot perform audio separation without remote API access.")
                job_log.info(f"Audio separator API: {api_url}")
                add_span_attribute("audio_separator_api", api_url)
```

**New code:**
```python
                # Determine separation mode: local GPU or remote API
                api_url = os.environ.get("AUDIO_SEPARATOR_API_URL")
                model_dir = os.environ.get("MODEL_DIR")
                if api_url:
                    job_log.info(f"Audio separator mode: remote API at {api_url}")
                    add_span_attribute("audio_separator_mode", "remote")
                    add_span_attribute("audio_separator_api", api_url)
                elif model_dir:
                    job_log.info(f"Audio separator mode: local GPU (models at {model_dir})")
                    add_span_attribute("audio_separator_mode", "local_gpu")
                    add_span_attribute("model_dir", model_dir)
                else:
                    raise Exception(
                        "Audio separation not configured. Set either AUDIO_SEPARATOR_API_URL "
                        "(remote API) or MODEL_DIR (local GPU with baked-in models)."
                    )
```

Then, where `create_audio_processor` is called (~line 369), pass `model_file_dir`:

**Old code:**
```python
                audio_processor = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model=job.clean_instrumental_model,
                    backing_vocals_models=job.backing_vocals_models,
                    other_stems_models=job.other_stems_models,
                )
```

**New code:**
```python
                audio_processor = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model=job.clean_instrumental_model,
                    backing_vocals_models=job.backing_vocals_models,
                    other_stems_models=job.other_stems_models,
                    model_file_dir=model_dir,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_processor_local_gpu.py -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 5: Run existing audio tests for regressions**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_remote.py -v 2>&1 | tail -20`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu
git add backend/workers/audio_worker.py tests/unit/test_audio_processor_local_gpu.py
git commit -m "feat: support local GPU separation when MODEL_DIR is set"
```

---

## Task 3: Wire ensemble presets into local Separator path

**Files:**
- Modify: `karaoke_gen/audio_processor.py:168-309`
- Modify: `tests/unit/test_audio_processor_local_gpu.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_audio_processor_local_gpu.py`:

```python
from unittest.mock import patch, MagicMock
from karaoke_gen.audio_processor import AudioProcessor


class TestLocalSeparationWithPresets:
    """Tests for local audio separation using ensemble presets."""

    def setup_method(self):
        self.processor = AudioProcessor(
            logger=logging.getLogger("test"),
            log_level=logging.DEBUG,
            log_formatter=None,
            model_file_dir="/models",
            lossless_output_format="FLAC",
            clean_instrumental_model="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
            backing_vocals_models=["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
            other_stems_models=[],
            ffmpeg_base_command="ffmpeg",
        )
        self.processor.instrumental_preset = "instrumental_clean"
        self.processor.karaoke_preset = "karaoke"

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.Separator")
    def test_local_separation_uses_ensemble_preset_for_stage1(self, mock_separator_cls):
        """Stage 1 uses instrumental_preset on the Separator constructor."""
        # Remove AUDIO_SEPARATOR_API_URL to force local path
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)

        mock_sep = MagicMock()
        mock_sep.separate.return_value = ["/tmp/vocals.flac", "/tmp/instrumental.flac"]
        mock_separator_cls.return_value = mock_sep

        with patch.object(self.processor, '_normalize_audio_files'):
            with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                self.processor.process_audio_separation(
                    "/tmp/test.wav", "Artist - Title", "/tmp/output"
                )

        # Verify Separator was constructed with ensemble_preset="instrumental_clean"
        constructor_kwargs = mock_separator_cls.call_args_list[0]
        assert constructor_kwargs[1].get("ensemble_preset") == "instrumental_clean"

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.Separator")
    def test_local_separation_uses_karaoke_preset_for_stage2(self, mock_separator_cls):
        """Stage 2 uses karaoke_preset on a fresh Separator for BV separation."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)

        mock_sep = MagicMock()
        # Stage 1 returns vocals + instrumental
        mock_sep.separate.return_value = ["/tmp/vocals.flac", "/tmp/instrumental.flac"]
        mock_separator_cls.return_value = mock_sep

        with patch.object(self.processor, '_normalize_audio_files'):
            with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                # Need vocals file to exist for stage 2
                with patch('os.path.isfile', return_value=True):
                    self.processor.process_audio_separation(
                        "/tmp/test.wav", "Artist - Title", "/tmp/output"
                    )

        # Separator should be instantiated twice: once for stage 1, once for stage 2
        assert mock_separator_cls.call_count == 2
        stage2_kwargs = mock_separator_cls.call_args_list[1]
        assert stage2_kwargs[1].get("ensemble_preset") == "karaoke"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_processor_local_gpu.py::TestLocalSeparationWithPresets -v 2>&1 | tail -20`
Expected: FAIL — the current local path does not pass `ensemble_preset` to `Separator()`

- [ ] **Step 3: Refactor local separation to use ensemble presets**

In `karaoke_gen/audio_processor.py`, replace the local separation path (inside `process_audio_separation`, the `else` branch at line ~197 through the `finally` block at ~308).

The key change: instead of calling `_separate_clean_instrumental` / `_separate_backing_vocals` (which use individual `load_model` + `separate` calls), create a `Separator` with `ensemble_preset` and call `separate()` once per stage.

**Replace the local path (lines ~197-308) with:**

```python
        else:
            self.logger.info("AUDIO_SEPARATOR_API_URL not set, using local audio separation. "
                           "Set this environment variable to use remote GPU processing.")

        from audio_separator.separator import Separator

        self.logger.info(f"Starting local audio separation process for {artist_title}")

        # Define lock file path in system temp directory
        lock_file_path = os.path.join(tempfile.gettempdir(), "audio_separator.lock")

        # Try to acquire lock (unchanged — harmless in Cloud Run Jobs, one container per execution)
        while True:
            try:
                if os.path.exists(lock_file_path):
                    try:
                        with open(lock_file_path, "r") as f:
                            lock_data = json.load(f)
                            pid = lock_data.get("pid")
                            start_time = datetime.fromisoformat(lock_data.get("start_time"))
                            running_track = lock_data.get("track")
                            if not psutil.pid_exists(pid):
                                self.logger.warning(f"Found stale lock from dead process {pid}, removing...")
                                os.remove(lock_file_path)
                            else:
                                runtime = datetime.now() - start_time
                                runtime_mins = runtime.total_seconds() / 60
                                try:
                                    proc = psutil.Process(pid)
                                    cmdline_args = proc.cmdline()
                                    cmd = " ".join(arg.decode('utf-8', errors='replace') if isinstance(arg, bytes) else arg for arg in cmdline_args)
                                except (psutil.AccessDenied, psutil.NoSuchProcess):
                                    cmd = "<command unavailable>"
                                self.logger.info(
                                    f"Waiting for other audio separation process...\n"
                                    f"  Track: {running_track}\n"
                                    f"  PID: {pid}\n"
                                    f"  Running time: {runtime_mins:.1f} minutes\n"
                                    f"  Command: {cmd}"
                                )
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        self.logger.warning(f"Found invalid lock file, removing: {e}")
                        os.remove(lock_file_path)

                lock_file = open(lock_file_path, "w")
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_data = {
                    "pid": os.getpid(),
                    "start_time": datetime.now().isoformat(),
                    "track": f"{artist_title}",
                }
                json.dump(lock_data, lock_file)
                lock_file.flush()
                break
            except IOError as e:
                if e.errno != errno.EAGAIN:
                    raise
                time.sleep(30)
                continue

        try:
            stems_dir = self._create_stems_directory(track_output_dir)
            result = {"clean_instrumental": {}, "other_stems": {}, "backing_vocals": {}, "combined_instrumentals": {}}

            if os.environ.get("KARAOKE_GEN_SKIP_AUDIO_SEPARATION"):
                return result

            # Check if we have ensemble presets configured
            instrumental_preset = getattr(self, 'instrumental_preset', None)
            karaoke_preset = getattr(self, 'karaoke_preset', None)

            if instrumental_preset:
                # Stage 1: Ensemble preset mode — higher quality, uses multiple models
                self.logger.info(f"Stage 1: Using ensemble preset '{instrumental_preset}'")
                separator = Separator(
                    log_level=self.log_level,
                    log_formatter=self.log_formatter,
                    model_file_dir=self.model_file_dir,
                    output_format=self.lossless_output_format,
                    output_dir=stems_dir,
                    ensemble_preset=instrumental_preset,
                )
                output_files = separator.separate(audio_file)
                self.logger.info(f"Stage 1 ensemble produced {len(output_files)} files: {output_files}")

                # Find vocals and instrumental from ensemble output
                for f in output_files:
                    basename = os.path.basename(f).lower()
                    if "vocal" in basename:
                        result["clean_instrumental"]["vocals"] = f
                    elif "instrumental" in basename or "no_vocal" in basename:
                        # Move instrumental to track_output_dir per convention
                        final_path = os.path.join(track_output_dir, os.path.basename(f))
                        if f != final_path:
                            shutil.move(f, final_path)
                        result["clean_instrumental"]["instrumental"] = final_path
            else:
                # Legacy mode — individual model calls
                separator = Separator(
                    log_level=self.log_level,
                    log_formatter=self.log_formatter,
                    model_file_dir=self.model_file_dir,
                    output_format=self.lossless_output_format,
                )
                result["clean_instrumental"] = self._separate_clean_instrumental(
                    separator, audio_file, artist_title, track_output_dir, stems_dir
                )
                result["other_stems"] = self._separate_other_stems(separator, audio_file, artist_title, stems_dir)

            # Stage 2: Backing vocals separation
            vocals_path = result["clean_instrumental"].get("vocals")
            has_backing_config = karaoke_preset or self.backing_vocals_models

            if vocals_path and has_backing_config and os.path.isfile(vocals_path):
                if karaoke_preset:
                    self.logger.info(f"Stage 2: Using ensemble preset '{karaoke_preset}'")
                    bv_separator = Separator(
                        log_level=self.log_level,
                        log_formatter=self.log_formatter,
                        model_file_dir=self.model_file_dir,
                        output_format=self.lossless_output_format,
                        output_dir=stems_dir,
                        ensemble_preset=karaoke_preset,
                    )
                    bv_output_files = bv_separator.separate(vocals_path)
                    self.logger.info(f"Stage 2 ensemble produced {len(bv_output_files)} files: {bv_output_files}")

                    bv_key = karaoke_preset
                    result["backing_vocals"][bv_key] = {}
                    for f in bv_output_files:
                        basename = os.path.basename(f).lower()
                        if "vocal" in basename and "backing" not in basename and "no_" not in basename:
                            result["backing_vocals"][bv_key]["lead_vocals"] = f
                        elif "instrumental" in basename or "backing" in basename or "no_vocal" in basename:
                            result["backing_vocals"][bv_key]["backing_vocals"] = f
                else:
                    # Legacy: individual model calls
                    result["backing_vocals"] = self._separate_backing_vocals(
                        separator, vocals_path, artist_title, stems_dir
                    )

            # Combined instrumentals + normalization (same as before)
            if result["clean_instrumental"].get("instrumental") and result["backing_vocals"]:
                result["combined_instrumentals"] = self._generate_combined_instrumentals(
                    result["clean_instrumental"]["instrumental"], result["backing_vocals"], artist_title, track_output_dir
                )
            self._normalize_audio_files(result, artist_title, track_output_dir)

            self.logger.info("Audio separation, combination, and normalization process completed")
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            try:
                os.remove(lock_file_path)
            except OSError:
                pass
```

**Important:** This replaces the entire local path. The remote API path (`_process_audio_separation_remote`) is untouched — it still runs when `AUDIO_SEPARATOR_API_URL` is set.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio_processor_local_gpu.py -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 5: Run all audio tests for regressions**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu && python -m pytest tests/unit/test_audio.py tests/unit/test_audio_remote.py -v 2>&1 | tail -30`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu
git add karaoke_gen/audio_processor.py tests/unit/test_audio_processor_local_gpu.py
git commit -m "feat: wire ensemble presets into local Separator path"
```

---

## Task 4: Create GPU base Docker image

**Files:**
- Create: `backend/Dockerfile.gpu-base`
- Create: `backend/scripts/download_models.py`
- Create: `backend/cloudbuild-gpu-base.yaml`

- [ ] **Step 1: Create the model download script**

Create `backend/scripts/download_models.py`:

```python
#!/usr/bin/env python3
"""
Download ensemble preset models at Docker build time.

Bakes models into the image so they're available immediately at runtime,
avoiding cold-start model download latency in Cloud Run Jobs.

Usage: python backend/scripts/download_models.py /models
"""
import sys
import logging
from audio_separator.separator import Separator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Presets to bake into the GPU image
PRESETS_TO_DOWNLOAD = ["instrumental_clean", "karaoke"]


def download_preset_models(model_dir: str) -> None:
    """Download all models for the configured presets."""
    for preset in PRESETS_TO_DOWNLOAD:
        logger.info(f"Downloading models for preset: {preset}")
        sep = Separator(
            model_file_dir=model_dir,
            output_format="FLAC",
            ensemble_preset=preset,
        )
        # load_model triggers the download for each model in the preset
        sep.load_model()
        logger.info(f"Preset '{preset}' models downloaded to {model_dir}")


if __name__ == "__main__":
    model_dir = sys.argv[1] if len(sys.argv) > 1 else "/models"
    download_preset_models(model_dir)
    logger.info(f"All preset models downloaded to {model_dir}")
```

- [ ] **Step 2: Create the GPU base Dockerfile**

Create `backend/Dockerfile.gpu-base`:

```dockerfile
# GPU base image for audio worker Cloud Run Job
#
# Contains everything from karaoke-backend-base PLUS:
# - NVIDIA CUDA 12.6 runtime
# - PyTorch with CUDA support
# - ONNX Runtime GPU
# - audio-separator[gpu] package
# - Pre-downloaded ensemble preset models (~1.5GB)
#
# Build with: gcloud builds submit --config=backend/cloudbuild-gpu-base.yaml
# Or locally: docker build -f backend/Dockerfile.gpu-base -t karaoke-backend-gpu-base .

FROM nvidia/cuda:12.6.3-runtime-ubuntu22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.11 from deadsnakes PPA
RUN apt-get update && apt-get install -y \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3.11-distutils \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# Install pip
RUN python3 -m ensurepip --upgrade && \
    python3 -m pip install --upgrade pip

# Install system dependencies (matches karaoke-backend-base)
RUN apt-get update && apt-get install -y \
    libsndfile1 \
    libsox-dev \
    sox \
    build-essential \
    curl \
    xz-utils \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install comprehensive font support (matches karaoke-backend-base)
RUN apt-get update && apt-get install -y \
    fonts-noto-core \
    fonts-noto-cjk \
    fonts-noto-extra \
    fonts-noto-color-emoji \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# Install optimized static FFmpeg build (matches karaoke-backend-base)
RUN curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz && \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp && \
    cp /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ && \
    cp /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    rm -rf /tmp/ffmpeg* && \
    ffmpeg -version

# Configure CUDA library paths
RUN echo "/usr/local/cuda/lib64" > /etc/ld.so.conf.d/cuda.conf && ldconfig
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}
ENV PATH=/usr/local/cuda/bin:${PATH}

# Set working directory
WORKDIR /app

# Copy dependency specification files
COPY pyproject.toml README.md LICENSE /app/

# Create minimal package stubs for editable install
RUN mkdir -p /app/karaoke_gen /app/lyrics_transcriber_temp /app/backend && \
    touch /app/karaoke_gen/__init__.py /app/lyrics_transcriber_temp/__init__.py /app/backend/__init__.py

# Install all Python dependencies including GPU extras
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -e ".[gpu]" || pip install -e .

# Install audio-separator with GPU support
RUN pip install "audio-separator[gpu]"

# Copy source code (AFTER deps installed)
COPY karaoke_gen /app/karaoke_gen
COPY lyrics_transcriber_temp /app/lyrics_transcriber_temp
COPY backend /app/backend

# Download spaCy language model
RUN python -m spacy download en_core_web_sm

# Download and bake in ensemble preset models
RUN mkdir -p /models
COPY backend/scripts/download_models.py /app/backend/scripts/download_models.py
RUN python /app/backend/scripts/download_models.py /models

# Label with build info
ARG BUILD_DATE
ARG BUILD_ID
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${BUILD_ID}"
LABEL org.opencontainers.image.title="karaoke-backend-gpu-base"
LABEL org.opencontainers.image.description="GPU base image with CUDA, PyTorch, and audio separation models"
```

- [ ] **Step 3: Create Cloud Build config**

Create `backend/cloudbuild-gpu-base.yaml`:

```yaml
# Cloud Build config for GPU base image
#
# Builds the CUDA + PyTorch + models base image used by the audio worker.
# This image is large (~10GB) so builds take 15-30 minutes.
#
# Trigger manually:
#   gcloud builds submit --config=backend/cloudbuild-gpu-base.yaml --project=nomadkaraoke --timeout=3600s
#
# The app-layer Dockerfile uses this as its BASE_IMAGE for audio worker deploys.

steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '-f'
      - 'backend/Dockerfile.gpu-base'
      - '-t'
      - 'us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend-gpu-base:latest'
      - '-t'
      - 'us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend-gpu-base:$BUILD_ID'
      - '--build-arg'
      - 'BUILD_DATE=$_BUILD_DATE'
      - '--build-arg'
      - 'BUILD_ID=$BUILD_ID'
      - '.'

images:
  - 'us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend-gpu-base:latest'
  - 'us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend-gpu-base:$BUILD_ID'

timeout: '3600s'

options:
  machineType: 'E2_HIGHCPU_32'
  diskSizeGb: 100
```

- [ ] **Step 4: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu
git add backend/Dockerfile.gpu-base backend/scripts/download_models.py backend/cloudbuild-gpu-base.yaml
git commit -m "feat: add GPU base Docker image for audio worker"
```

---

## Task 5: Add GPU Artifact Registry and update audio worker infra

**Files:**
- Create: `infrastructure/modules/gpu_artifact_registry.py`
- Modify: `infrastructure/modules/cloud_run.py:163-252`
- Modify: `infrastructure/__main__.py:148`

- [ ] **Step 1: Create GPU Artifact Registry module**

Create `infrastructure/modules/gpu_artifact_registry.py`:

```python
"""
Artifact Registry for GPU Docker images.

Located in us-east4 (co-located with L4 GPU instances).
Stores the karaoke-backend-gpu base image used by the audio worker.
"""

import pulumi_gcp as gcp
from pulumi_gcp import artifactregistry

from config import PROJECT_ID

GPU_REGION = "us-east4"


def create_gpu_artifact_repo() -> artifactregistry.Repository:
    """Create Artifact Registry repo for GPU Docker images in us-east4."""
    return artifactregistry.Repository(
        "karaoke-backend-gpu-artifact-repo",
        repository_id="karaoke-backend-gpu",
        location=GPU_REGION,
        format="DOCKER",
        description="Docker repository for GPU-enabled karaoke-backend images (audio worker)",
    )
```

- [ ] **Step 2: Modify audio worker Cloud Run Job for GPU**

In `infrastructure/modules/cloud_run.py`, update `create_audio_separation_job`:

```python
# Add at the top of the file, after existing imports
AUDIO_WORKER_GPU_REGION = "us-east4"  # L4 GPU quota available here


def create_audio_separation_job(
    bucket: gcp.storage.Bucket,
    service_account: gcp.serviceaccount.Account,
) -> cloudrunv2.Job:
    """
    Create the Cloud Run Job for audio separation with L4 GPU.

    Runs the Separator class directly with GPU acceleration, eliminating
    the HTTP call to the separate audio-separator service.

    Deployed in us-east4 where L4 GPU quota is available.

    Typical duration: 5-10 minutes (local GPU separation)

    Args:
        bucket: The GCS bucket for job artifacts.
        service_account: The service account to run the job.

    Returns:
        cloudrunv2.Job: The Cloud Run Job resource.
    """
    audio_separation_job = cloudrunv2.Job(
        "audio-separation-job",
        name="audio-separation-job",
        location=AUDIO_WORKER_GPU_REGION,
        template=cloudrunv2.JobTemplateArgs(
            template=cloudrunv2.JobTemplateTemplateArgs(
                containers=[
                    cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=f"{AUDIO_WORKER_GPU_REGION}-docker.pkg.dev/{PROJECT_ID}/karaoke-backend-gpu/karaoke-backend:latest",
                        args=["python", "-m", "backend.workers.audio_worker"],
                        resources=cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "cpu": "4",
                                "memory": "16Gi",
                                "nvidia.com/gpu": "1",
                            },
                        ),
                        envs=[
                            # Admin token for Cloud Tasks auth header
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ADMIN_TOKENS",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/admin-tokens",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Model directory (baked into GPU image, triggers local GPU mode)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="MODEL_DIR",
                                value="/models",
                            ),
                            # Cloud Run service URL for Cloud Tasks targeting
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="CLOUD_RUN_SERVICE_URL",
                                value="https://api.nomadkaraoke.com",
                            ),
                            # Enable Cloud Tasks mode (vs direct HTTP)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENABLE_CLOUD_TASKS",
                                value="true",
                            ),
                            # Basic configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=AUDIO_WORKER_GPU_REGION,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                        ],
                    )
                ],
                node_selector=cloudrunv2.JobTemplateTemplateNodeSelectorArgs(
                    accelerator="nvidia-l4",  # L4 GPU
                ),
                service_account=service_account.email,
                timeout="1800s",  # 30 minutes (local GPU is faster than remote API)
                max_retries=2,
            ),
        ),
    )

    return audio_separation_job
```

Key changes from existing:
- `location` → `AUDIO_WORKER_GPU_REGION` (`us-east4`)
- `image` → GPU image from us-east4 registry
- `resources` → `cpu: 4`, `memory: 16Gi`, `nvidia.com/gpu: 1`
- `envs` → replaced `AUDIO_SEPARATOR_API_URL` with `MODEL_DIR=/models`
- Added `node_selector` with `nvidia-l4`
- `timeout` → `1800s` (30 min, down from 40 — local GPU is faster)

- [ ] **Step 3: Wire GPU Artifact Registry into __main__.py**

In `infrastructure/__main__.py`, add the import and resource creation:

After line 42 (`from modules import audio_separator_service`), add:
```python
from modules import gpu_artifact_registry
```

After line 56 (`artifact_repo = artifact_registry.create_repository()`), add:
```python
# GPU Artifact Registry in us-east4 (for audio worker GPU images)
gpu_artifact_repo = gpu_artifact_registry.create_gpu_artifact_repo()
```

After the existing Cloud Build IAM section (~line 86), add:
```python
# Grant Cloud Build access to GPU Artifact Registry (us-east4)
gpu_artifact_registry_iam = artifactregistry.RepositoryIamBinding(
    "cloudbuild-gpu-artifact-registry-access",
    repository=gpu_artifact_repo.name,
    location=gpu_artifact_repo.location,
    role="roles/artifactregistry.writer",
    members=cloudbuild_service_accounts,
)
```

- [ ] **Step 4: Verify Pulumi config parses correctly**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu/infrastructure && python -c "import __main__" 2>&1 | tail -5`

Note: This will fail if Pulumi runtime isn't available, but it validates Python imports and syntax. If import errors appear, fix them.

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu
git add infrastructure/modules/gpu_artifact_registry.py infrastructure/modules/cloud_run.py infrastructure/__main__.py
git commit -m "feat: add GPU infra for audio worker (L4 in us-east4)"
```

---

## Task 6: Update docs and version

**Files:**
- Modify: `docs/ARCHITECTURE.md` (audio separation section)
- Modify: `docs/LESSONS-LEARNED.md`
- Modify: `pyproject.toml` (version bump)

- [ ] **Step 1: Update ARCHITECTURE.md**

Find the audio separation section in `docs/ARCHITECTURE.md` and update it to reflect the new architecture:

- Audio worker now runs with L4 GPU in us-east4
- Runs `Separator` class directly (no HTTP hop)
- Old audio-separator service kept for rollback

- [ ] **Step 2: Add lessons learned**

Add an entry to `docs/LESSONS-LEARNED.md`:

```markdown
### Direct GPU in Cloud Run Jobs > HTTP Microservice

When a Cloud Run Job needs GPU processing, attach the GPU directly to the Job instead of calling a separate GPU service over HTTP. Benefits:
- No HTTP timeout concerns (Jobs can run up to 24h)
- No connection management, retry logic, or polling
- Simpler architecture (1 hop instead of 3)
- Platform handles scaling naturally (each Job execution gets its own GPU)

The audio separator started as a separate Cloud Run Service called via HTTP from the audio worker Job. This required 1800s HTTP timeouts, complex retry logic, and caused a production outage when the fire-and-forget pattern interacted badly with Cloud Run's autoscaler. Moving the GPU work directly into the Job eliminated all of this complexity.
```

- [ ] **Step 3: Bump version**

In `pyproject.toml`, bump the patch version.

- [ ] **Step 4: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu
git add docs/ARCHITECTURE.md docs/LESSONS-LEARNED.md pyproject.toml
git commit -m "docs: update architecture for direct GPU audio separation"
```

---

## Task 7: Build, deploy, and verify in production

This task is NOT automated — it requires manual steps with real infrastructure.

- [ ] **Step 1: Build the GPU base image**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu
gcloud builds submit --config=backend/cloudbuild-gpu-base.yaml --project=nomadkaraoke --timeout=3600s
```

This takes 15-30 minutes. Monitor with: `gcloud builds list --project=nomadkaraoke --limit=1`

- [ ] **Step 2: Build the app-layer GPU image**

Build the regular app Dockerfile but with the GPU base:

```bash
# Tag for the GPU audio worker
docker build -f backend/Dockerfile \
  --build-arg BASE_IMAGE=us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend-gpu-base:latest \
  -t us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend:latest \
  .

docker push us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend:latest
```

- [ ] **Step 3: Apply Pulumi infrastructure**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-audio-sep-direct-gpu/infrastructure
pulumi up
```

Review the diff carefully. Key changes:
- Audio worker job moves to us-east4 with GPU
- New Artifact Registry repo in us-east4
- `AUDIO_SEPARATOR_API_URL` removed, `MODEL_DIR` added

- [ ] **Step 4: Submit a test job with known input**

Use the test file `python-audio-separator/tests/inputs/under_pressure_harmonies.flac` to submit a full 2-phase separation job (instrumental + karaoke/BV). This file has clear harmonies, making it good for verifying both separation stages.

```bash
export KARAOKE_ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)

# Upload test file to GCS
gsutil cp /Users/andrew/Projects/nomadkaraoke/python-audio-separator/tests/inputs/under_pressure_harmonies.flac \
  gs://nomadkaraoke-karaoke-gen/test-audio/under_pressure_harmonies.flac

# Submit a test job via the API (artist/title for file naming)
curl -X POST "https://api.nomadkaraoke.com/api/jobs" \
  -H "X-Admin-Token: $KARAOKE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "artist": "Queen & David Bowie",
    "title": "Under Pressure (Harmonies Test)",
    "input_media_gcs_path": "test-audio/under_pressure_harmonies.flac"
  }'

# Monitor: Check Cloud Run Jobs console for audio-separation-job in us-east4
# Logs: gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="audio-separation-job"' --project=nomadkaraoke --limit=50 --format='table(timestamp,textPayload)'
```

- [ ] **Step 5: Verify output quality**

Verify the job completed successfully and all expected stems were generated:
- **Stage 1 outputs:** clean instrumental (mixed_instrumental.flac) + mixed vocals (mixed_vocals.flac)
- **Stage 2 outputs:** lead vocals (lead_vocals.flac) + backing vocals (backing_vocals.flac)
- **Post-processing:** combined instrumentals, normalized audio files
- **GCS:** all stems uploaded correctly to the job's GCS path

Compare output quality against a previous job processed via the old HTTP separator service to confirm the direct GPU path produces equivalent results.
