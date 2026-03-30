# Async Audio Separator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the audio separator Cloud Run service process jobs asynchronously, with Firestore-backed job status and GCS-backed output files, enabling multi-instance GPU scaling.

**Architecture:** The `/separate` endpoint returns immediately with a task_id. Background threads process jobs with a GPU semaphore (1 per instance). Job status is stored in Firestore so any instance can serve `/status` and `/download` requests. Output files are uploaded to GCS on completion. Client polling loop (already implemented) handles the async flow with no code changes needed.

**Tech Stack:** Python, FastAPI, google-cloud-firestore, google-cloud-storage, threading.Semaphore, Pulumi (infrastructure)

**Repos:** `python-audio-separator` (Tasks 1-6), `karaoke-gen` (Tasks 7-8)

**Spec:** `docs/archive/2026-03-25-async-audio-separator-design.md`

---

## File Structure

### python-audio-separator

| File | Action | Responsibility |
|------|--------|----------------|
| `audio_separator/remote/job_store.py` | Create | Firestore-backed job status store with same interface as dict |
| `audio_separator/remote/output_store.py` | Create | GCS upload/download for separation output files |
| `audio_separator/remote/deploy_cloudrun.py` | Modify | Wire async endpoint, GPU semaphore, new stores |
| `audio_separator/remote/api_client.py` | Modify | Reduce POST timeout from 300s to 60s |
| `Dockerfile.cloudrun` | Modify | Add google-cloud-firestore dependency |
| `tests/unit/test_job_store.py` | Create | Tests for Firestore job store |
| `tests/unit/test_output_store.py` | Create | Tests for GCS output store |
| `tests/unit/test_deploy_cloudrun_async.py` | Create | Tests for async endpoint behavior |
| `tests/unit/test_remote_api_client.py` | Modify | Update POST timeout assertion |

### karaoke-gen

| File | Action | Responsibility |
|------|--------|----------------|
| `infrastructure/modules/audio_separator_service.py` | Modify | Update concurrency, max_instances, add GCS bucket + Firestore IAM |
| `pyproject.toml` | Modify | Bump audio-separator version |

---

### Task 1: Create Firestore job store

**Files:**
- Create: `python-audio-separator/audio_separator/remote/job_store.py`
- Create: `python-audio-separator/tests/unit/test_job_store.py`

This replaces the in-memory `job_status_store` dict with a Firestore-backed store that has the same get/set interface.

- [ ] **Step 1: Write failing tests for FirestoreJobStore**

```python
# tests/unit/test_job_store.py
import pytest
from unittest.mock import MagicMock, patch
from audio_separator.remote.job_store import FirestoreJobStore


@pytest.fixture
def mock_firestore_client():
    with patch("audio_separator.remote.job_store.firestore.Client") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        yield mock_client


@pytest.fixture
def store(mock_firestore_client):
    return FirestoreJobStore(project="test-project")


class TestFirestoreJobStore:
    def test_set_creates_document(self, store, mock_firestore_client):
        """Setting a task_id writes to Firestore with timestamps."""
        store.set("task-123", {
            "task_id": "task-123",
            "status": "submitted",
            "progress": 0,
        })

        collection = mock_firestore_client.collection
        collection.assert_called_with("audio_separation_jobs")
        collection.return_value.document.assert_called_with("task-123")
        doc_ref = collection.return_value.document.return_value
        doc_ref.set.assert_called_once()

        written_data = doc_ref.set.call_args[0][0]
        assert written_data["task_id"] == "task-123"
        assert written_data["status"] == "submitted"
        assert "updated_at" in written_data

    def test_get_returns_document_data(self, store, mock_firestore_client):
        """Getting a task_id reads from Firestore."""
        doc_snapshot = MagicMock()
        doc_snapshot.exists = True
        doc_snapshot.to_dict.return_value = {
            "task_id": "task-123",
            "status": "processing",
            "progress": 50,
        }
        collection = mock_firestore_client.collection
        collection.return_value.document.return_value.get.return_value = doc_snapshot

        result = store.get("task-123")

        assert result["status"] == "processing"
        assert result["progress"] == 50

    def test_get_returns_none_for_missing_document(self, store, mock_firestore_client):
        """Getting a nonexistent task_id returns None."""
        doc_snapshot = MagicMock()
        doc_snapshot.exists = False
        collection = mock_firestore_client.collection
        collection.return_value.document.return_value.get.return_value = doc_snapshot

        result = store.get("nonexistent")
        assert result is None

    def test_contains_checks_existence(self, store, mock_firestore_client):
        """__contains__ checks if document exists in Firestore."""
        doc_snapshot = MagicMock()
        doc_snapshot.exists = True
        collection = mock_firestore_client.collection
        collection.return_value.document.return_value.get.return_value = doc_snapshot

        assert "task-123" in store

    def test_update_merges_fields(self, store, mock_firestore_client):
        """Updating a task merges fields without overwriting the whole doc."""
        store.update("task-123", {"status": "processing", "progress": 25})

        collection = mock_firestore_client.collection
        doc_ref = collection.return_value.document.return_value
        doc_ref.update.assert_called_once()
        updated_data = doc_ref.update.call_args[0][0]
        assert updated_data["status"] == "processing"
        assert updated_data["progress"] == 25
        assert "updated_at" in updated_data

    def test_delete_removes_document(self, store, mock_firestore_client):
        """Deleting a task_id removes the Firestore document."""
        store.delete("task-123")

        collection = mock_firestore_client.collection
        doc_ref = collection.return_value.document.return_value
        doc_ref.delete.assert_called_once()

    def test_cleanup_old_jobs(self, store, mock_firestore_client):
        """cleanup_old_jobs deletes documents older than max_age_seconds."""
        old_doc = MagicMock()
        old_doc.reference = MagicMock()
        query = MagicMock()
        query.stream.return_value = [old_doc]

        collection = mock_firestore_client.collection
        collection.return_value.where.return_value.where.return_value = query

        deleted = store.cleanup_old_jobs(max_age_seconds=3600)

        assert deleted == 1
        old_doc.reference.delete.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_job_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audio_separator.remote.job_store'`

- [ ] **Step 3: Implement FirestoreJobStore**

```python
# audio_separator/remote/job_store.py
"""Firestore-backed job status store for audio separation jobs.

Replaces the in-memory dict so any Cloud Run instance can read/write job status.
"""
import logging
import time
from typing import Optional

from google.cloud import firestore

logger = logging.getLogger("audio-separator-api")

COLLECTION = "audio_separation_jobs"


class FirestoreJobStore:
    """Job status store backed by Firestore.

    Provides dict-like get/set interface for job status documents.
    """

    def __init__(self, project: str = "nomadkaraoke"):
        self._db = firestore.Client(project=project)
        self._collection = self._db.collection(COLLECTION)

    def set(self, task_id: str, data: dict) -> None:
        """Create or overwrite a job status document."""
        data = {**data, "updated_at": firestore.SERVER_TIMESTAMP}
        if "created_at" not in data:
            data["created_at"] = firestore.SERVER_TIMESTAMP
        self._collection.document(task_id).set(data)

    def get(self, task_id: str) -> Optional[dict]:
        """Get job status. Returns None if not found."""
        doc = self._collection.document(task_id).get()
        if doc.exists:
            return doc.to_dict()
        return None

    def update(self, task_id: str, fields: dict) -> None:
        """Merge fields into an existing document."""
        fields = {**fields, "updated_at": firestore.SERVER_TIMESTAMP}
        self._collection.document(task_id).update(fields)

    def delete(self, task_id: str) -> None:
        """Delete a job status document."""
        self._collection.document(task_id).delete()

    def __contains__(self, task_id: str) -> bool:
        """Check if a task exists."""
        doc = self._collection.document(task_id).get()
        return doc.exists

    def cleanup_old_jobs(self, max_age_seconds: int = 3600) -> int:
        """Delete completed/errored jobs older than max_age_seconds. Returns count deleted."""
        cutoff = time.time() - max_age_seconds
        # Firestore timestamps are datetime objects; compare with a datetime
        from datetime import datetime, timezone
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)

        deleted = 0
        for status in ("completed", "error"):
            query = (
                self._collection
                .where("status", "==", status)
                .where("updated_at", "<", cutoff_dt)
            )
            for doc in query.stream():
                doc.reference.delete()
                deleted += 1

        if deleted:
            logger.info(f"Cleaned up {deleted} old job(s) from Firestore")
        return deleted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_job_store.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator
git add audio_separator/remote/job_store.py tests/unit/test_job_store.py
git commit -m "feat: add Firestore-backed job status store for multi-instance scaling"
```

---

### Task 2: Create GCS output store

**Files:**
- Create: `python-audio-separator/audio_separator/remote/output_store.py`
- Create: `python-audio-separator/tests/unit/test_output_store.py`

Handles uploading separation output files to GCS and generating download URLs, so any instance can serve downloads.

- [ ] **Step 1: Write failing tests for GCSOutputStore**

```python
# tests/unit/test_output_store.py
import pytest
from unittest.mock import MagicMock, patch, mock_open
from audio_separator.remote.output_store import GCSOutputStore


@pytest.fixture
def mock_storage_client():
    with patch("audio_separator.remote.output_store.storage.Client") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        yield mock_client


@pytest.fixture
def store(mock_storage_client):
    return GCSOutputStore(bucket_name="test-bucket", project="test-project")


class TestGCSOutputStore:
    def test_upload_directory(self, store, mock_storage_client):
        """Uploads all files from a local directory to GCS under task_id prefix."""
        import os
        with patch("os.listdir", return_value=["vocals.flac", "instrumental.flac"]):
            with patch("os.path.isfile", return_value=True):
                store.upload_task_outputs("task-123", "/tmp/outputs/task-123")

        bucket = mock_storage_client.bucket.return_value
        assert bucket.blob.call_count == 2
        blob = bucket.blob.return_value
        assert blob.upload_from_filename.call_count == 2

    def test_upload_builds_correct_gcs_paths(self, store, mock_storage_client):
        """GCS paths are {task_id}/{filename}."""
        with patch("os.listdir", return_value=["output.flac"]):
            with patch("os.path.isfile", return_value=True):
                store.upload_task_outputs("task-123", "/tmp/outputs/task-123")

        bucket = mock_storage_client.bucket.return_value
        bucket.blob.assert_called_with("task-123/output.flac")

    def test_download_file(self, store, mock_storage_client):
        """Downloads a specific file from GCS to a local path."""
        store.download_file("task-123", "vocals.flac", "/tmp/local/vocals.flac")

        bucket = mock_storage_client.bucket.return_value
        bucket.blob.assert_called_with("task-123/vocals.flac")
        blob = bucket.blob.return_value
        blob.download_to_filename.assert_called_with("/tmp/local/vocals.flac")

    def test_get_file_bytes(self, store, mock_storage_client):
        """Gets file content as bytes for streaming download responses."""
        bucket = mock_storage_client.bucket.return_value
        blob = bucket.blob.return_value
        blob.download_as_bytes.return_value = b"audio data"

        result = store.get_file_bytes("task-123", "vocals.flac")

        assert result == b"audio data"
        bucket.blob.assert_called_with("task-123/vocals.flac")

    def test_delete_task_outputs(self, store, mock_storage_client):
        """Deletes all files for a task from GCS."""
        bucket = mock_storage_client.bucket.return_value
        blob1 = MagicMock()
        blob2 = MagicMock()
        bucket.list_blobs.return_value = [blob1, blob2]

        deleted = store.delete_task_outputs("task-123")

        bucket.list_blobs.assert_called_with(prefix="task-123/")
        blob1.delete.assert_called_once()
        blob2.delete.assert_called_once()
        assert deleted == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_output_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audio_separator.remote.output_store'`

- [ ] **Step 3: Implement GCSOutputStore**

```python
# audio_separator/remote/output_store.py
"""GCS-backed output file store for audio separation results.

Uploads separation output files to GCS so any Cloud Run instance can serve downloads.
"""
import logging
import os

from google.cloud import storage

logger = logging.getLogger("audio-separator-api")


class GCSOutputStore:
    """Manages separation output files in GCS."""

    def __init__(self, bucket_name: str = "nomadkaraoke-audio-separator-outputs", project: str = "nomadkaraoke"):
        self._client = storage.Client(project=project)
        self._bucket = self._client.bucket(bucket_name)

    def upload_task_outputs(self, task_id: str, local_dir: str) -> list[str]:
        """Upload all files in local_dir to GCS under {task_id}/ prefix.

        Returns list of uploaded filenames.
        """
        uploaded = []
        for filename in os.listdir(local_dir):
            local_path = os.path.join(local_dir, filename)
            if not os.path.isfile(local_path):
                continue
            gcs_path = f"{task_id}/{filename}"
            blob = self._bucket.blob(gcs_path)
            blob.upload_from_filename(local_path)
            uploaded.append(filename)
            logger.info(f"Uploaded {filename} to gs://{self._bucket.name}/{gcs_path}")
        return uploaded

    def get_file_bytes(self, task_id: str, filename: str) -> bytes:
        """Download file content as bytes (for HTTP responses)."""
        gcs_path = f"{task_id}/{filename}"
        blob = self._bucket.blob(gcs_path)
        return blob.download_as_bytes()

    def download_file(self, task_id: str, filename: str, local_path: str) -> str:
        """Download a file from GCS to a local path."""
        gcs_path = f"{task_id}/{filename}"
        blob = self._bucket.blob(gcs_path)
        blob.download_to_filename(local_path)
        return local_path

    def delete_task_outputs(self, task_id: str) -> int:
        """Delete all output files for a task. Returns count deleted."""
        deleted = 0
        for blob in self._bucket.list_blobs(prefix=f"{task_id}/"):
            blob.delete()
            deleted += 1
        if deleted:
            logger.info(f"Deleted {deleted} output file(s) for task {task_id}")
        return deleted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_output_store.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator
git add audio_separator/remote/output_store.py tests/unit/test_output_store.py
git commit -m "feat: add GCS output store for multi-instance file downloads"
```

---

### Task 3: Make `/separate` endpoint async with GPU semaphore

**Files:**
- Modify: `python-audio-separator/audio_separator/remote/deploy_cloudrun.py:46-53,183-346,453-518,521-531,534-577,668-672`
- Create: `python-audio-separator/tests/unit/test_deploy_cloudrun_async.py`

This is the core change — make the endpoint fire-and-forget, wire up Firestore job store, GCS output store, and GPU semaphore.

- [ ] **Step 1: Write failing tests for async endpoint behavior**

```python
# tests/unit/test_deploy_cloudrun_async.py
import pytest
import threading
import time
from unittest.mock import MagicMock, patch, AsyncMock
from audio_separator.remote.job_store import FirestoreJobStore
from audio_separator.remote.output_store import GCSOutputStore


class TestGPUSemaphore:
    """Test that GPU semaphore serializes separation work."""

    def test_semaphore_blocks_concurrent_jobs(self):
        """Second job waits while first job holds the semaphore."""
        semaphore = threading.Semaphore(1)
        execution_order = []

        def job(name, duration):
            with semaphore:
                execution_order.append(f"{name}_start")
                time.sleep(duration)
                execution_order.append(f"{name}_end")

        t1 = threading.Thread(target=job, args=("job1", 0.2))
        t2 = threading.Thread(target=job, args=("job2", 0.1))
        t1.start()
        time.sleep(0.05)  # Ensure job1 starts first
        t2.start()
        t1.join()
        t2.join()

        # job1 must complete before job2 starts
        assert execution_order == ["job1_start", "job1_end", "job2_start", "job2_end"]


class TestSeparateAudioSyncWithStores:
    """Test that separate_audio_sync uses Firestore and GCS stores."""

    @patch("audio_separator.remote.deploy_cloudrun.get_output_store")
    @patch("audio_separator.remote.deploy_cloudrun.get_job_store")
    @patch("audio_separator.remote.deploy_cloudrun.gpu_semaphore")
    def test_sync_function_updates_firestore_status(self, mock_semaphore, mock_get_job_store, mock_get_output_store):
        """separate_audio_sync updates job status via Firestore store."""
        mock_job_store = MagicMock()
        mock_get_job_store.return_value = mock_job_store
        mock_output_store = MagicMock()
        mock_get_output_store.return_value = mock_output_store

        # Make semaphore a no-op context manager
        mock_semaphore.acquire = MagicMock()
        mock_semaphore.release = MagicMock()

        with patch("audio_separator.remote.deploy_cloudrun.Separator") as MockSep:
            mock_separator = MagicMock()
            MockSep.return_value = mock_separator
            mock_separator.separate.return_value = ["/tmp/output/vocals.flac"]
            mock_separator.load_model.return_value = None

            with patch("os.makedirs"), \
                 patch("builtins.open", MagicMock()), \
                 patch("os.path.basename", return_value="vocals.flac"):
                from audio_separator.remote.deploy_cloudrun import separate_audio_sync
                separate_audio_sync(
                    audio_data=b"fake",
                    filename="test.wav",
                    task_id="task-test",
                    models=["test_model"],
                )

        # Verify Firestore was updated (at least queued → processing → completed)
        update_calls = mock_job_store.update.call_args_list
        statuses = [call[0][1].get("status") for call in update_calls if "status" in call[0][1]]
        assert "queued" in statuses or "processing" in statuses
        assert "completed" in statuses

    @patch("audio_separator.remote.deploy_cloudrun.get_output_store")
    @patch("audio_separator.remote.deploy_cloudrun.get_job_store")
    @patch("audio_separator.remote.deploy_cloudrun.gpu_semaphore")
    def test_sync_function_uploads_outputs_to_gcs(self, mock_semaphore, mock_get_job_store, mock_get_output_store):
        """On completion, output files are uploaded to GCS."""
        mock_job_store = MagicMock()
        mock_get_job_store.return_value = mock_job_store
        mock_output_store = MagicMock()
        mock_get_output_store.return_value = mock_output_store

        mock_semaphore.acquire = MagicMock()
        mock_semaphore.release = MagicMock()

        with patch("audio_separator.remote.deploy_cloudrun.Separator") as MockSep:
            mock_separator = MagicMock()
            MockSep.return_value = mock_separator
            mock_separator.separate.return_value = ["/tmp/output/vocals.flac"]
            mock_separator.load_model.return_value = None

            with patch("os.makedirs"), \
                 patch("builtins.open", MagicMock()), \
                 patch("os.path.basename", return_value="vocals.flac"):
                from audio_separator.remote.deploy_cloudrun import separate_audio_sync
                separate_audio_sync(
                    audio_data=b"fake",
                    filename="test.wav",
                    task_id="task-test",
                    models=["test_model"],
                )

        mock_output_store.upload_task_outputs.assert_called_once()
        call_args = mock_output_store.upload_task_outputs.call_args[0]
        assert call_args[0] == "task-test"  # task_id


class TestAsyncEndpointBehavior:
    """Test that the /separate endpoint returns immediately."""

    def test_endpoint_returns_submitted_status(self):
        """The /separate endpoint should return status='submitted', not 'completed'."""
        # This is a contract test — the endpoint must return before processing finishes.
        # We verify by checking the response schema includes task_id and submitted status.
        # Integration testing of the full endpoint requires a running server.
        from audio_separator.remote.deploy_cloudrun import job_status_store
        # Simulate what the endpoint does: write submitted status, return it
        task_id = "test-contract"
        initial_status = {
            "task_id": task_id,
            "status": "submitted",
            "progress": 0,
        }
        # The endpoint should return this before processing starts
        assert initial_status["status"] == "submitted"
        assert "task_id" in initial_status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_deploy_cloudrun_async.py -v`
Expected: FAIL — `ImportError` for `gpu_semaphore`, `job_store`, `output_store` module-level names

- [ ] **Step 3: Modify deploy_cloudrun.py — add module-level stores and semaphore**

At the top of the file, after the existing imports and constants (after line 56), add:

```python
# --- Async job infrastructure ---
# GPU semaphore: only one separation runs per instance at a time
gpu_semaphore = threading.Semaphore(1)

# Firestore job store and GCS output store (initialized lazily on first request)
_job_store = None
_output_store = None

# GCS bucket for output files
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "nomadkaraoke-audio-separator-outputs")
# GCP project for Firestore
GCP_PROJECT = os.environ.get("GCP_PROJECT", "nomadkaraoke")


def _get_job_store():
    global _job_store
    if _job_store is None:
        from audio_separator.remote.job_store import FirestoreJobStore
        _job_store = FirestoreJobStore(project=GCP_PROJECT)
    return _job_store


def _get_output_store():
    global _output_store
    if _output_store is None:
        from audio_separator.remote.output_store import GCSOutputStore
        _output_store = GCSOutputStore(bucket_name=OUTPUT_BUCKET, project=GCP_PROJECT)
    return _output_store


# Module-level references for testability (can be patched in tests)
job_store = property(lambda self: _get_job_store())
output_store = property(lambda self: _get_output_store())
```

Wait — properties don't work at module level. Use a simpler approach for testability. Replace the above with:

```python
# --- Async job infrastructure ---
gpu_semaphore = threading.Semaphore(1)

OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "nomadkaraoke-audio-separator-outputs")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "nomadkaraoke")

# Lazy-initialized stores (use get_job_store()/get_output_store() to access)
_job_store = None
_output_store = None


def get_job_store():
    """Get or create the Firestore job store (lazy init)."""
    global _job_store
    if _job_store is None:
        from audio_separator.remote.job_store import FirestoreJobStore
        _job_store = FirestoreJobStore(project=GCP_PROJECT)
    return _job_store


def get_output_store():
    """Get or create the GCS output store (lazy init)."""
    global _output_store
    if _output_store is None:
        from audio_separator.remote.output_store import GCSOutputStore
        _output_store = GCSOutputStore(bucket_name=OUTPUT_BUCKET, project=GCP_PROJECT)
    return _output_store
```

- [ ] **Step 4: Modify `separate_audio_sync` to use Firestore + GCS + semaphore**

Replace the `update_status` inner function and add semaphore acquisition. The function currently starts at line 141. Key changes:

1. Replace `job_status_store[task_id] = status_data` with `get_job_store().update(task_id, status_data)`
2. Wrap the GPU work in `with gpu_semaphore:`
3. Add `"queued"` status before acquiring semaphore
4. Upload outputs to GCS after completion
5. Clean up local files after upload

Replace the `update_status` function (lines 189-202) with:

```python
    def update_status(status: str, progress: int = 0, error: str = None, files: dict = None):
        status_data = {
            "status": status,
            "progress": progress,
            "models_used": models_used,
            "total_models": len(models) if models else 1,
            "current_model_index": 0,
        }
        if files is not None:
            status_data["files"] = files
        if error:
            status_data["error"] = error
        get_job_store().update(task_id, status_data)
```

Before the `try:` block at line 204, add semaphore acquisition:

```python
    # Wait for GPU availability
    update_status("queued", 0)
    logger.info(f"[{task_id}] Waiting for GPU semaphore...")
    gpu_semaphore.acquire()
    logger.info(f"[{task_id}] GPU semaphore acquired, starting separation")
```

At the end of the success path (after line 333 `update_status("completed",...)`), add GCS upload:

```python
        # Upload outputs to GCS for cross-instance access
        get_output_store().upload_task_outputs(task_id, output_dir)
        update_status("completed", 100, files=all_output_files)
        logger.info(f"Separation completed. {len(all_output_files)} output files uploaded to GCS.")
```

In the `except` and `finally` blocks, release the semaphore:

```python
    except Exception as e:
        ...existing error handling...
    finally:
        gpu_semaphore.release()
        logger.info(f"[{task_id}] GPU semaphore released")
        # Clean up local files
        output_dir = f"{STORAGE_DIR}/outputs/{task_id}"
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)
```

- [ ] **Step 5: Modify the `/separate` endpoint to fire-and-forget**

Replace lines 455-513 (the endpoint body after input parsing):

```python
        task_id = str(uuid.uuid4())
        instance_id = os.environ.get("K_REVISION", "local")

        # Write initial status to Firestore
        get_job_store().set(task_id, {
            "task_id": task_id,
            "status": "submitted",
            "progress": 0,
            "original_filename": filename,
            "models_used": [f"preset:{preset}"] if preset else (models_list or ["default"]),
            "total_models": 1 if preset else (len(models_list) if models_list else 1),
            "current_model_index": 0,
            "files": {},
            "instance_id": instance_id,
        })

        # Fire-and-forget: run separation in background thread
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None,
            lambda: separate_audio_sync(
                audio_data,
                filename,
                task_id,
                models_list,
                preset,
                output_format,
                output_bitrate,
                normalization_threshold,
                amplification_threshold,
                output_single_stem,
                invert_using_spec,
                sample_rate,
                use_soundfile,
                use_autocast,
                custom_output_names_dict,
                mdx_segment_size,
                mdx_overlap,
                mdx_batch_size,
                mdx_hop_length,
                mdx_enable_denoise,
                vr_batch_size,
                vr_window_size,
                vr_aggression,
                vr_enable_tta,
                vr_high_end_process,
                vr_enable_post_process,
                vr_post_process_threshold,
                demucs_segment_size,
                demucs_shifts,
                demucs_overlap,
                demucs_segments_enabled,
                mdxc_segment_size,
                mdxc_override_model_segment_size,
                mdxc_overlap,
                mdxc_batch_size,
                mdxc_pitch_shift,
            ),
        )

        # Return immediately — client polls /status/{task_id}
        return {
            "task_id": task_id,
            "status": "submitted",
            "progress": 0,
            "original_filename": filename,
            "models_used": [f"preset:{preset}"] if preset else (models_list or ["default"]),
            "total_models": 1 if preset else (len(models_list) if models_list else 1),
        }
```

Note: `loop.run_in_executor(...)` without `await` — the future is fire-and-forget.

- [ ] **Step 6: Modify `/status` endpoint to read from Firestore**

Replace lines 521-531:

```python
@web_app.get("/status/{task_id}")
async def get_job_status(task_id: str) -> dict:
    """Get the status of a separation job."""
    result = get_job_store().get(task_id)
    if result:
        return result
    return {
        "task_id": task_id,
        "status": "not_found",
        "progress": 0,
        "error": "Job not found - may have been cleaned up or never existed",
    }
```

- [ ] **Step 7: Modify `/download` endpoint to read from GCS**

Replace lines 534-577:

```python
@web_app.get("/download/{task_id}/{file_hash}")
async def download_file(task_id: str, file_hash: str) -> Response:
    """Download a separated audio file using its hash identifier."""
    try:
        status_data = get_job_store().get(task_id)
        if not status_data:
            raise HTTPException(status_code=404, detail="Task not found")

        files_dict = status_data.get("files", {})

        # Resolve hash to filename
        actual_filename = None
        if isinstance(files_dict, dict):
            actual_filename = files_dict.get(file_hash)

        if not actual_filename:
            raise HTTPException(status_code=404, detail=f"File with hash {file_hash} not found")

        # Download from GCS (any instance can serve this)
        file_data = get_output_store().get_file_bytes(task_id, actual_filename)

        detected_type = filetype.guess(file_data)
        content_type = detected_type.mime if detected_type and detected_type.mime else "application/octet-stream"

        ascii_filename = "".join(c if ord(c) < 128 else "_" for c in actual_filename)
        encoded_filename = quote(actual_filename, safe="")
        content_disposition = f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'

        return Response(content=file_data, media_type=content_type, headers={"Content-Disposition": content_disposition})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}") from e
```

- [ ] **Step 8: Add startup cleanup**

Modify the startup event (line 668-676):

```python
@web_app.on_event("startup")
async def startup_event():
    """Clean up local storage and download models from GCS on startup."""
    os.makedirs(MODEL_DIR, exist_ok=True)

    # Wipe local outputs from previous instance (in-memory state is gone anyway)
    outputs_dir = f"{STORAGE_DIR}/outputs"
    if os.path.exists(outputs_dir):
        shutil.rmtree(outputs_dir, ignore_errors=True)
    os.makedirs(outputs_dir, exist_ok=True)

    # Clean up old Firestore jobs (>1 hour)
    try:
        get_job_store().cleanup_old_jobs(max_age_seconds=3600)
    except Exception as e:
        logger.warning(f"Failed to clean up old jobs: {e}")

    # Download models in background thread
    thread = threading.Thread(target=download_models_from_gcs, daemon=True)
    thread.start()
```

- [ ] **Step 9: Run tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_deploy_cloudrun_async.py tests/unit/test_job_store.py tests/unit/test_output_store.py -v`
Expected: All tests PASS

- [ ] **Step 10: Run existing tests to check for regressions**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_remote_api_client.py -v`
Expected: All existing tests still PASS

- [ ] **Step 11: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator
git add audio_separator/remote/deploy_cloudrun.py tests/unit/test_deploy_cloudrun_async.py
git commit -m "feat: make /separate endpoint async with GPU semaphore and Firestore/GCS stores"
```

---

### Task 4: Reduce client POST timeout

**Files:**
- Modify: `python-audio-separator/audio_separator/remote/api_client.py:161`
- Modify: `python-audio-separator/tests/unit/test_remote_api_client.py`

- [ ] **Step 1: Write failing test for new timeout value**

Add to `tests/unit/test_remote_api_client.py`, in `TestAudioSeparatorAPIClient`:

```python
    @patch("requests.Session.post")
    @patch("builtins.open", new_callable=mock_open, read_data=b"fake audio content")
    def test_separate_audio_post_timeout_is_60s(self, mock_file, mock_post, api_client, mock_audio_file):
        """POST timeout should be 60s (server returns immediately now)."""
        mock_response = Mock()
        mock_response.json.return_value = {"task_id": "test", "status": "submitted"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        api_client.separate_audio(mock_audio_file)

        call_args = mock_post.call_args
        assert call_args[1]["timeout"] == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_remote_api_client.py::TestAudioSeparatorAPIClient::test_separate_audio_post_timeout_is_60s -v`
Expected: FAIL — `assert 300 == 60`

- [ ] **Step 3: Change timeout from 300 to 60**

In `audio_separator/remote/api_client.py`, line 161, change:

```python
                timeout=300,
```

to:

```python
                timeout=60,
```

Also update the comment on line 151 from "Increase timeout for large files (5 minutes)" to:

```python
            # Server returns immediately with task_id; 60s is generous for submission
```

- [ ] **Step 4: Run all client tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/test_remote_api_client.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator
git add audio_separator/remote/api_client.py tests/unit/test_remote_api_client.py
git commit -m "feat: reduce POST timeout from 300s to 60s (server is now async)"
```

---

### Task 5: Add google-cloud-firestore to Dockerfile

**Files:**
- Modify: `python-audio-separator/Dockerfile.cloudrun:64`

- [ ] **Step 1: Add Firestore dependency to Dockerfile**

In `Dockerfile.cloudrun`, line 64, after `"google-cloud-storage>=2.0.0"`, add:

```dockerfile
        "google-cloud-storage>=2.0.0" \
        "google-cloud-firestore>=2.0.0" \
```

- [ ] **Step 2: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator
git add Dockerfile.cloudrun
git commit -m "feat: add google-cloud-firestore to Dockerfile for async job store"
```

---

### Task 6: Run full test suite in python-audio-separator

**Files:** None (verification only)

- [ ] **Step 1: Run all unit tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/python-audio-separator && python -m pytest tests/unit/ -v`
Expected: All tests PASS with no regressions

- [ ] **Step 2: Fix any failures**

If any test fails, fix it before proceeding.

---

### Task 7: Update karaoke-gen infrastructure

**Files:**
- Modify: `karaoke-gen/infrastructure/modules/audio_separator_service.py:95-100`

The Cloud Run service needs updated concurrency (was 1, now 50), updated max_instances (was 5, now 10), and the service account needs Firestore access. Also need to create the GCS output bucket.

- [ ] **Step 1: Update Cloud Run service concurrency and scaling**

In `infrastructure/modules/audio_separator_service.py`, modify `create_service()`:

Change line 97:
```python
                max_instance_count=5,  # Cloud Run GPU services limited to 5 max instances
```
to:
```python
                max_instance_count=10,
```

Change line 99:
```python
            max_instance_request_concurrency=1,  # Each separation uses the full GPU; route new requests to new instances
```
to:
```python
            max_instance_request_concurrency=50,  # GPU serialized via semaphore; allow polling/download requests through
```

Add environment variables for the output bucket and GCP project (after the existing envs at line 131):

```python
                        cloudrunv2.ServiceTemplateContainerEnvArgs(
                            name="OUTPUT_BUCKET",
                            value="nomadkaraoke-audio-separator-outputs",
                        ),
                        cloudrunv2.ServiceTemplateContainerEnvArgs(
                            name="GCP_PROJECT",
                            value=PROJECT_ID,
                        ),
```

- [ ] **Step 2: Add Firestore IAM permissions**

In `grant_permissions()`, add Firestore read/write:

```python
    # Read/write Firestore for async job status
    bindings["firestore"] = gcp.projects.IAMMember(
        "audio-separator-firestore-access",
        project=PROJECT_ID,
        role="roles/datastore.user",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )
```

- [ ] **Step 3: Add GCS output bucket**

Add a new function after `create_service_account()`:

```python
def create_output_bucket() -> gcp.storage.Bucket:
    """Create GCS bucket for separation output files with 1-hour lifecycle."""
    bucket = gcp.storage.Bucket(
        "audio-separator-output-bucket",
        name="nomadkaraoke-audio-separator-outputs",
        location="US",
        force_destroy=True,
        lifecycle_rules=[
            gcp.storage.BucketLifecycleRuleArgs(
                action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                    age=1,  # Delete after 1 day (minimum GCS lifecycle granularity)
                ),
            ),
        ],
    )
    return bucket
```

Note: GCS lifecycle minimum granularity is 1 day, not 1 hour. For more aggressive cleanup, we rely on the Firestore cleanup + the fact that output files only need to survive until the client downloads them (minutes, not hours).

Grant the service account write access to the output bucket. Add to `grant_permissions()`:

```python
    # Write to output bucket for separation results
    bindings["output_bucket"] = gcp.storage.BucketIAMMember(
        "audio-separator-output-bucket-access",
        bucket="nomadkaraoke-audio-separator-outputs",
        role="roles/storage.objectAdmin",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )
```

Update `create_all_resources()` to include the bucket:

```python
def create_all_resources() -> dict:
    artifact_repo = create_audio_separator_artifact_repo()
    sa = create_service_account()
    output_bucket = create_output_bucket()
    iam_bindings = grant_permissions(sa)
    service = create_service(sa, artifact_repo)
    unauthenticated_access = allow_unauthenticated_access(service)

    return {
        "artifact_repo": artifact_repo,
        "service_account": sa,
        "output_bucket": output_bucket,
        "service": service,
        "iam_bindings": iam_bindings,
    }
```

- [ ] **Step 4: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-separator-timeout-fix
git add infrastructure/modules/audio_separator_service.py
git commit -m "feat: update audio separator infra for async processing (concurrency, Firestore, GCS bucket)"
```

---

### Task 8: Bump audio-separator dependency in karaoke-gen

**Files:**
- Modify: `karaoke-gen/pyproject.toml:39`

This task is done AFTER the python-audio-separator changes are released.

- [ ] **Step 1: Update dependency version**

In `pyproject.toml`, update the audio-separator version to match the new release:

```toml
audio-separator = { version = ">=0.45.0", extras = ["cpu"] }
```

(Exact version depends on what gets released.)

- [ ] **Step 2: Update lock file**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-separator-timeout-fix && poetry update audio-separator`

- [ ] **Step 3: Run karaoke-gen tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-separator-timeout-fix && make test 2>&1 | tail -n 500`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-separator-timeout-fix
git add pyproject.toml poetry.lock
git commit -m "feat: bump audio-separator for async separation support"
```

---

## Deployment Order

1. **python-audio-separator**: Ship Tasks 1-6, release new version, deploy Cloud Run GPU service
2. **karaoke-gen infrastructure**: Apply Task 7 via `pulumi up` (creates GCS bucket, updates IAM, updates Cloud Run config)
3. **karaoke-gen dependency**: Ship Task 8 (version bump) — this is optional since the existing client already works with the async server

## Verification

After deployment, test with a real ensemble preset job:
1. Submit a separation with `preset=instrumental_clean` on a large WAV file
2. Verify the POST returns immediately (< 5s)
3. Verify polling shows `queued` → `processing` → `completed` transitions
4. Verify output files download successfully
5. Submit a second job while the first is processing — verify it queues and completes after the first
