"""Regression tests for review-prefixed API aliases on the local ReviewServer.

The unified Next.js frontend calls cloud-style `/api/review/{jobId}/...` URLs.
In local CLI mode the same frontend bundle is served by `ReviewServer`, so
those endpoints must route to the existing local handlers. Drift between the
cloud backend (`backend/api/routes/review.py`) and this server has repeatedly
broken the local review flow; these tests pin each alias so future drift is
caught in CI.
"""
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karaoke_gen.lyrics_transcriber.review.server import ReviewServer
from karaoke_gen.lyrics_transcriber.types import CorrectionResult


def _flac_bytes() -> bytes:
    """Tiny valid FLAC magic — enough for FileResponse to stream."""
    return b"fLaC" + b"\x00" * 256


def _mp3_bytes() -> bytes:
    return b"ID3\x03\x00\x00\x00" + b"\x00" * 256


@pytest.fixture
def audio_files(tmp_path: Path) -> dict:
    """Write placeholder audio files for the four streaming endpoints."""
    paths = {}
    for name, content in (
        ("main.mp3", _mp3_bytes()),
        ("clean.flac", _flac_bytes()),
        ("with_backing.flac", _flac_bytes()),
        ("backing_vocals.flac", _flac_bytes()),
    ):
        p = tmp_path / name
        p.write_bytes(content)
        paths[name] = str(p)
    return paths


@pytest.fixture
def fake_frontend(tmp_path: Path) -> Path:
    """Minimal static-export layout so ReviewServer can mount its frontend."""
    root = tmp_path / "out"
    root.mkdir()
    (root / "index.html").write_text("<html>root</html>")
    (root / "_next" / "static" / "chunks").mkdir(parents=True)
    return root


@pytest.fixture
def client(fake_frontend, audio_files, monkeypatch):
    """Build a ReviewServer with a real CorrectionResult and audio stems."""
    monkeypatch.setattr(
        "karaoke_gen.nextjs_frontend.get_nextjs_assets_dir",
        lambda: fake_frontend,
    )
    monkeypatch.setattr(
        "karaoke_gen.nextjs_frontend.is_nextjs_frontend_available",
        lambda: True,
    )

    from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
    cache_dir = fake_frontend.parent / "cache"
    cache_dir.mkdir()
    output_config = OutputConfig(
        output_styles_json="",
        cache_dir=str(cache_dir),
        output_dir=str(cache_dir),
    )

    correction_result = CorrectionResult(
        original_segments=[],
        corrected_segments=[],
        corrections=[],
        corrections_made=0,
        confidence=1.0,
        reference_lyrics={},
        anchor_sequences=[],
        gap_sequences=[],
        resized_segments=[],
        metadata={"audio_hash": "abc123", "artist": "A", "title": "T"},
        correction_steps=[],
        word_id_map={},
        segment_id_map={},
    )

    server = ReviewServer(
        correction_result=correction_result,
        output_config=output_config,
        audio_filepath=audio_files["main.mp3"],
        logger=logging.getLogger("test"),
        clean_instrumental_path=audio_files["clean.flac"],
        with_backing_path=audio_files["with_backing.flac"],
        backing_vocals_path=audio_files["backing_vocals.flac"],
    )
    return TestClient(server.app)


class TestReviewHandlersAlias:
    """`/api/review/{job_id}/handlers` must accept POST (matching the cloud
    backend and the frontend client)."""

    def test_handlers_post_is_routed(self, client):
        r = client.post("/api/review/local/handlers", json=[])
        # The route must match — 200/500 is fine, 405 means the method is
        # wrong (which is the regression we're guarding against).
        assert r.status_code != 405, r.text

    def test_handlers_patch_returns_405(self, client):
        """Cloud frontend never sends PATCH — explicit guard against drift."""
        r = client.patch("/api/review/local/handlers", json=[])
        assert r.status_code == 405


class TestReviewAudioStemAlias:
    """`/api/review/{job_id}/audio/{stem_or_hash}` must serve both stems and
    the original audio-by-hash without the frontend needing to know which."""

    def test_clean_stem_streams_from_review_path(self, client):
        r = client.get("/api/review/local/audio/clean")
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"fLaC")

    def test_with_backing_stem_streams_from_review_path(self, client):
        r = client.get("/api/review/local/audio/with_backing")
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"fLaC")

    def test_backing_vocals_stem_streams_from_review_path(self, client):
        r = client.get("/api/review/local/audio/backing_vocals")
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"fLaC")

    def test_audio_hash_still_streams_from_review_path(self, client):
        """The same route must still serve the primary audio by hash."""
        r = client.get("/api/review/local/audio/abc123")
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"ID3")

    def test_unknown_hash_or_stem_returns_404(self, client):
        r = client.get("/api/review/local/audio/not-a-hash-or-stem")
        assert r.status_code == 404


class TestReviewAnnotationsAlias:
    """`POST /api/review/{job_id}/v1/annotations` is called on review submit.

    The cloud backend accepts either a single annotation dict or the batch
    form `{"annotations": [...]}`. The local server only needs to not 404 —
    annotation storage is optional and may not be wired up in CLI mode.
    """

    def test_batch_annotations_does_not_404(self, client):
        payload = {
            "annotations": [
                {"ai_correction_id": "c1", "reviewer_action": "accepted", "reason_category": "ok"}
            ]
        }
        r = client.post("/api/review/local/v1/annotations", json=payload)
        assert r.status_code != 404, r.text

    def test_empty_batch_returns_success(self, client):
        r = client.post("/api/review/local/v1/annotations", json={"annotations": []})
        assert r.status_code in (200, 201), r.text


def _session_payload(marker: str = "v1", edits: int = 1) -> dict:
    return {
        "correction_data": {
            "corrected_segments": [{"id": "s0", "text": f"line {marker}"}],
            "metadata": {"marker": marker},
        },
        "edit_count": edits,
        "trigger": "auto",
        "summary": {
            "total_segments": 1,
            "total_words": 2,
            "corrections_made": edits,
            "changed_words": [],
        },
    }


class TestReviewSessionsPersistence:
    """End-to-end persistence: save → list → get → delete via the HTTP API.

    Guards the contract the unified Next.js frontend expects: response
    shapes must line up with `ReviewSession` and `ReviewSessionWithData`
    so the restore dialog populates correctly.
    """

    def test_list_returns_empty_when_no_sessions_saved(self, client):
        r = client.get("/api/review/local/sessions")
        assert r.status_code == 200, r.text
        assert r.json() == {"sessions": []}

    def test_save_then_list_returns_metadata_without_correction_data(self, client):
        r = client.post("/api/review/local/sessions", json=_session_payload())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        session_id = body["session_id"]
        assert session_id

        listed = client.get("/api/review/local/sessions").json()["sessions"]
        assert len(listed) == 1
        meta = listed[0]
        assert meta["session_id"] == session_id
        assert meta["edit_count"] == 1
        assert meta["trigger"] == "auto"
        # Wire contract: the fields the frontend TypeScript expects must exist
        assert set(["created_at", "updated_at", "job_id", "user_email"]).issubset(
            meta.keys()
        )
        # List responses must not carry the heavy correction_data payload
        assert "correction_data" not in meta

    def test_save_dedupes_identical_consecutive_saves(self, client):
        # Auto-save timer spam: identical payloads must not create new sessions
        first = client.post("/api/review/local/sessions", json=_session_payload())
        second = client.post("/api/review/local/sessions", json=_session_payload())
        assert first.json()["status"] == "success"
        assert second.json()["status"] == "skipped"
        listed = client.get("/api/review/local/sessions").json()["sessions"]
        assert len(listed) == 1

    def test_save_persists_distinct_payloads(self, client):
        s1 = client.post("/api/review/local/sessions", json=_session_payload("v1", 1))
        s2 = client.post("/api/review/local/sessions", json=_session_payload("v2", 2))
        listed = client.get("/api/review/local/sessions").json()["sessions"]
        # Most-recent-first ordering for the restore dialog
        assert [s["session_id"] for s in listed] == [
            s2.json()["session_id"],
            s1.json()["session_id"],
        ]

    def test_get_returns_full_correction_data(self, client):
        saved = client.post(
            "/api/review/local/sessions", json=_session_payload("payload")
        ).json()
        r = client.get(f"/api/review/local/sessions/{saved['session_id']}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"] == saved["session_id"]
        assert body["correction_data"]["metadata"]["marker"] == "payload"

    def test_get_unknown_session_returns_404(self, client):
        r = client.get("/api/review/local/sessions/does-not-exist")
        assert r.status_code == 404

    def test_delete_removes_session(self, client):
        saved = client.post(
            "/api/review/local/sessions", json=_session_payload()
        ).json()
        d = client.delete(f"/api/review/local/sessions/{saved['session_id']}")
        assert d.status_code == 200, d.text
        assert client.get("/api/review/local/sessions").json()["sessions"] == []
        assert (
            client.get(f"/api/review/local/sessions/{saved['session_id']}").status_code
            == 404
        )

    def test_delete_unknown_still_returns_success(self, client):
        # Frontend treats 200 as idempotent; mirror that even on miss.
        r = client.delete("/api/review/local/sessions/never-existed")
        assert r.status_code == 200, r.text
