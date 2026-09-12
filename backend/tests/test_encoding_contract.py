"""Contract test: replay golden GCE worker responses through the real encode path.

This is the "kill mock drift" backstop (hardening plan P3). Every encoding mock
in the suite is hand-authored, and the real "cached / empty" response shape was
simply never in the corpus — the textbook cause of NOMAD-1632's Failure B. Here
we capture the *real* worker response shapes as golden JSON fixtures
(``backend/tests/fixtures/encoding/``) and replay them through the real
``classify_encoded_output`` and ``GCEEncodingBackend.encode()`` so the classifier
and the completeness guard are exercised against the actual contract.

Kept lightweight and deterministic: no network, no GCS, no filesystem writes —
the only boundary patched is the worker call (``encode_videos``).
"""

import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.encoding_interface import (
    GceEncodeResponse,
    GCEEncodingBackend,
    EncodingInput,
    classify_encoded_output,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "encoding"

# The four formats GCEEncodingBackend.encode() always requests (and the guard
# enforces). Keep in sync with encoding_interface.encode().
REQUESTED_FORMATS = {"mp4_4k_lossless", "mp4_4k_lossy", "mkv_4k", "mp4_720p"}


def _load(name: str) -> GceEncodeResponse:
    with open(FIXTURES_DIR / f"{name}.json") as fh:
        return json.load(fh)


async def _run_encode(response: GceEncodeResponse):
    """Drive the REAL encode() with a mocked worker returning ``response``."""
    backend = GCEEncodingBackend(logger=MagicMock())
    mock_service = MagicMock()
    mock_service.encode_videos = AsyncMock(return_value=response)

    input_config = EncodingInput(
        title_video_path="/tmp/title.mov",
        karaoke_video_path="/tmp/karaoke.mkv",
        instrumental_audio_path="/tmp/audio.flac",
        artist="Nat King Cole",
        title="Portrait of Jennie",
        options={
            "job_id": "NOMAD-1632",
            "input_gcs_path": "gs://bucket/jobs/NOMAD-1632/",
            "output_gcs_path": "gs://bucket/jobs/NOMAD-1632/finals/",
        },
    )

    with patch(
        "backend.services.encoding_service.get_encoding_service",
        return_value=mock_service,
    ):
        return await backend.encode(input_config)


class TestEncodingResponseContract:
    """Replay golden fixtures through the real classifier + encode()."""

    ALL_FIXTURES = ["success", "cached", "empty", "partial", "malformed"]

    @pytest.mark.parametrize("name", ALL_FIXTURES)
    def test_fixture_conforms_to_schema(self, name):
        """Every fixture matches the GceEncodeResponse contract keys/types."""
        data = _load(name)
        assert set(data).issubset(set(GceEncodeResponse.__annotations__)), (
            f"{name}.json has keys outside GceEncodeResponse"
        )
        assert isinstance(data["status"], str)
        assert isinstance(data["output_files"], list)
        assert all(isinstance(p, str) for p in data["output_files"])

    def test_classify_success_fixture(self):
        """NOMAD-1632 regression: 'Portrait of Jennie' 720p must NOT misclassify
        as the portrait video — classification keys off the trailing tag."""
        files = _load("success")["output_files"]
        classified = {classify_encoded_output(p) for p in files}
        assert classified == REQUESTED_FORMATS
        # The 720p whose TITLE contains 'Portrait' maps to mp4_720p, not portrait_mp4.
        seven20 = next(p for p in files if "720p" in p)
        assert classify_encoded_output(seven20) == "mp4_720p"

    def test_classify_cached_fixture_covers_all_branches(self):
        """The cached full-set fixture exercises every classifier branch."""
        files = _load("cached")["output_files"]
        classified = {classify_encoded_output(p) for p in files}
        assert classified == REQUESTED_FORMATS | {
            "portrait_mp4",
            "with_vocals_mp4",
            "title_mov",
            "end_mov",
            "cdg_zip",
        }

    def test_classify_malformed_fixture_classified_out(self):
        """Stray / wrong-extension names classify to None (no false slotting)."""
        files = _load("malformed")["output_files"]
        assert all(classify_encoded_output(p) is None for p in files)
        # Specifically: a 720p tag with the wrong extension must be rejected.
        wrong_ext = next(p for p in files if p.endswith(".webm"))
        assert classify_encoded_output(wrong_ext) is None

    @pytest.mark.asyncio
    async def test_encode_success_fixture(self):
        out = await _run_encode(_load("success"))
        assert out.success is True
        assert out.lossy_720p_mp4_path is not None
        assert set(out.output_files) == REQUESTED_FORMATS

    @pytest.mark.asyncio
    async def test_encode_cached_fixture(self):
        out = await _run_encode(_load("cached"))
        assert out.success is True
        assert out.portrait_mp4_path is not None
        assert out.with_vocals_mp4_path is not None
        assert REQUESTED_FORMATS.issubset(set(out.output_files))

    @pytest.mark.asyncio
    async def test_encode_empty_fixture_is_recoverable(self):
        """Empty output_files must NOT trip the guard — it's recoverable
        (stale cache) and the orchestrator re-encodes. Failure B defense."""
        out = await _run_encode(_load("empty"))
        assert out.success is True
        assert out.output_files == {}
        assert out.error_message is None

    @pytest.mark.asyncio
    async def test_encode_partial_fixture_trips_guard(self):
        """Partial (720p missing) must fail loud. Failure A defense."""
        out = await _run_encode(_load("partial"))
        assert out.success is False
        assert "mp4_720p" in out.error_message
        assert "incomplete" in out.error_message.lower()

    @pytest.mark.asyncio
    async def test_encode_malformed_fixture_treated_as_empty(self):
        """Nothing classifies into a slot → treated as empty (no guard trip,
        no crash), which the orchestrator then recovers from as a stale cache."""
        out = await _run_encode(_load("malformed"))
        assert out.success is True
        assert out.output_files == {}
        assert out.error_message is None
