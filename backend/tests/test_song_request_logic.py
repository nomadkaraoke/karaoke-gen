"""Pure-logic unit tests for the song-request board (no Firestore needed)."""
import re
from datetime import datetime, timezone

from backend.api.routes.requests_board import _to_public, _was_corrected
from backend.models.song_request import SongRequest
from backend.services.song_request_service import (
    SongRequestService,
    _dedupe_key,
    _utc_today,
)


def _req(**kw) -> SongRequest:
    d = dict(
        id="r1",
        artist="The Beatles",
        title="Hey Jude",
        artist_raw="The Beatles",
        title_raw="Hey Jude",
        dedupe_key=_dedupe_key("The Beatles", "Hey Jude"),
        submitted_by="a@b.com",
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    d.update(kw)
    return SongRequest(**d)


def test_dedupe_key_normalizes_case_accents_and_ampersand():
    # Case/whitespace/punct fold, and '&' == 'and'
    assert _dedupe_key("The Beatles", "Hey Jude") == _dedupe_key("  the beatles ", "hey  jude!")
    assert _dedupe_key("Simon & Garfunkel", "x") == _dedupe_key("Simon and Garfunkel", "x")
    assert _dedupe_key("Beyoncé", "Halo") == _dedupe_key("beyonce", "halo")


def test_dedupe_key_distinguishes_different_songs():
    assert _dedupe_key("The Beatles", "Hey Jude") != _dedupe_key("The Beatles", "Let It Be")


def test_utc_today_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", _utc_today())


def test_vote_doc_id_encodes_one_per_day():
    assert SongRequestService._vote_doc_id("a@b.com", "2026-09-02") == "a@b.com__2026-09-02"


def test_was_corrected_true_when_canonical_differs():
    assert _was_corrected(_req(artist_raw="beatles", artist="The Beatles")) is True


def test_was_corrected_false_when_only_cosmetic_whitespace():
    # Same after normalization → not flagged as a correction
    assert _was_corrected(_req(artist_raw="The Beatles", artist="The Beatles", title_raw="hey jude", title="Hey Jude")) is False


def test_to_public_hides_email_and_serializes():
    pub = _to_public(_req(vote_count=4), your_vote=1)
    dumped = pub.model_dump()
    assert "submitted_by" not in dumped
    assert dumped["vote_count"] == 4
    assert dumped["your_vote"] == 1
    assert dumped["created_at"] == "2026-09-02T00:00:00+00:00"
