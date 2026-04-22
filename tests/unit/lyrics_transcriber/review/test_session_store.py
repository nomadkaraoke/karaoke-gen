"""Unit tests for LocalReviewSessionStore.

JSON-on-disk persistence for the local ReviewServer's session history,
mirroring the cloud's Firestore+GCS shape with one file per session under
`{cache_dir}/review_sessions/{audio_hash}/`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from karaoke_gen.lyrics_transcriber.review.session_store import (
    LocalReviewSessionStore,
)


@pytest.fixture
def store(tmp_path: Path) -> LocalReviewSessionStore:
    return LocalReviewSessionStore(cache_dir=str(tmp_path))


def _correction_data(marker: str = "v1") -> dict:
    """Minimal payload shaped like the frontend's CorrectionData."""
    return {
        "corrected_segments": [{"id": "s0", "text": f"line {marker}"}],
        "corrections": [],
        "metadata": {"marker": marker},
    }


def _summary(edits: int = 1) -> dict:
    return {
        "total_segments": 1,
        "total_words": 2,
        "corrections_made": edits,
        "changed_words": [],
    }


class TestSave:
    def test_save_returns_new_session_id(self, store):
        result = store.save(
            audio_hash="abc",
            correction_data=_correction_data(),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        assert result["status"] == "success"
        assert isinstance(result["session_id"], str) and result["session_id"]
        assert "created_at" in result

    def test_save_writes_session_file_on_disk(self, store, tmp_path):
        result = store.save(
            audio_hash="abc",
            correction_data=_correction_data(),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        expected = tmp_path / "review_sessions" / "abc" / f"{result['session_id']}.json"
        assert expected.exists(), f"expected session file at {expected}"
        body = json.loads(expected.read_text())
        # Stored envelope must carry both metadata and the full correction_data
        assert body["session_id"] == result["session_id"]
        assert body["correction_data"]["metadata"]["marker"] == "v1"

    def test_save_isolates_sessions_per_audio_hash(self, store, tmp_path):
        store.save(
            audio_hash="song_a",
            correction_data=_correction_data("a"),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        store.save(
            audio_hash="song_b",
            correction_data=_correction_data("b"),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        assert store.list_sessions(audio_hash="song_a") and store.list_sessions(
            audio_hash="song_b"
        )
        # A's sessions must not leak into B's directory
        a_ids = {s["session_id"] for s in store.list_sessions(audio_hash="song_a")}
        b_ids = {s["session_id"] for s in store.list_sessions(audio_hash="song_b")}
        assert a_ids.isdisjoint(b_ids)


class TestDedup:
    def test_save_skips_duplicate_of_latest(self, store):
        first = store.save(
            audio_hash="abc",
            correction_data=_correction_data(),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        second = store.save(
            audio_hash="abc",
            correction_data=_correction_data(),  # identical payload
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        assert first["status"] == "success"
        assert second["status"] == "skipped"
        assert second["reason"] == "identical_data"

    def test_save_persists_when_data_differs(self, store):
        first = store.save(
            audio_hash="abc",
            correction_data=_correction_data("v1"),
            edit_count=1,
            trigger="auto",
            summary=_summary(1),
        )
        second = store.save(
            audio_hash="abc",
            correction_data=_correction_data("v2"),
            edit_count=2,
            trigger="auto",
            summary=_summary(2),
        )
        assert first["status"] == "success"
        assert second["status"] == "success"
        assert first["session_id"] != second["session_id"]


class TestList:
    def test_list_returns_empty_when_none_saved(self, store):
        assert store.list_sessions(audio_hash="abc") == []

    def test_list_excludes_full_correction_data(self, store):
        store.save(
            audio_hash="abc",
            correction_data=_correction_data(),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        sessions = store.list_sessions(audio_hash="abc")
        assert len(sessions) == 1
        assert "correction_data" not in sessions[0]
        # Metadata frontend needs for the restore dialog must still be there
        assert sessions[0]["edit_count"] == 1
        assert sessions[0]["trigger"] == "auto"
        assert sessions[0]["summary"]["corrections_made"] == 1

    def test_list_sorted_most_recent_first(self, store):
        # Force distinct created_at values by saving different payloads
        s1 = store.save(
            audio_hash="abc",
            correction_data=_correction_data("v1"),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        s2 = store.save(
            audio_hash="abc",
            correction_data=_correction_data("v2"),
            edit_count=2,
            trigger="auto",
            summary=_summary(),
        )
        s3 = store.save(
            audio_hash="abc",
            correction_data=_correction_data("v3"),
            edit_count=3,
            trigger="preview",
            summary=_summary(),
        )
        ids = [s["session_id"] for s in store.list_sessions(audio_hash="abc")]
        assert ids == [s3["session_id"], s2["session_id"], s1["session_id"]]


class TestGet:
    def test_get_returns_full_correction_data(self, store):
        saved = store.save(
            audio_hash="abc",
            correction_data=_correction_data("deep"),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        session = store.get_session(
            audio_hash="abc", session_id=saved["session_id"]
        )
        assert session is not None
        assert session["correction_data"]["metadata"]["marker"] == "deep"

    def test_get_unknown_returns_none(self, store):
        assert store.get_session(audio_hash="abc", session_id="nope") is None


class TestDelete:
    def test_delete_removes_file(self, store, tmp_path):
        saved = store.save(
            audio_hash="abc",
            correction_data=_correction_data(),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        path = tmp_path / "review_sessions" / "abc" / f"{saved['session_id']}.json"
        assert path.exists()
        assert store.delete_session(
            audio_hash="abc", session_id=saved["session_id"]
        ) is True
        assert not path.exists()

    def test_delete_unknown_returns_false(self, store):
        assert (
            store.delete_session(audio_hash="abc", session_id="missing")
            is False
        )

    def test_delete_does_not_affect_other_sessions(self, store):
        s1 = store.save(
            audio_hash="abc",
            correction_data=_correction_data("v1"),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        s2 = store.save(
            audio_hash="abc",
            correction_data=_correction_data("v2"),
            edit_count=2,
            trigger="auto",
            summary=_summary(),
        )
        store.delete_session(audio_hash="abc", session_id=s1["session_id"])
        remaining = {s["session_id"] for s in store.list_sessions(audio_hash="abc")}
        assert remaining == {s2["session_id"]}


class TestPersistenceAcrossInstances:
    """Sessions must survive an entire karaoke-gen invocation — that's the
    whole point of persistence vs. the previous in-memory stubs."""

    def test_new_store_instance_sees_prior_sessions(self, tmp_path):
        first = LocalReviewSessionStore(cache_dir=str(tmp_path))
        saved = first.save(
            audio_hash="abc",
            correction_data=_correction_data(),
            edit_count=1,
            trigger="auto",
            summary=_summary(),
        )
        second = LocalReviewSessionStore(cache_dir=str(tmp_path))
        sessions = second.list_sessions(audio_hash="abc")
        assert [s["session_id"] for s in sessions] == [saved["session_id"]]

        loaded = second.get_session(
            audio_hash="abc", session_id=saved["session_id"]
        )
        assert loaded is not None
        assert loaded["correction_data"]["metadata"]["marker"] == "v1"
