"""
Tests for BaseTranscriber's on-disk cache round-trip.

This is the mechanism the OutputConfig.cache_dir fix depends on: the transcriber
writes its raw + converted API responses into `cache_dir` (keyed by the audio
file's MD5), and a subsequent transcription of the same audio reads them back
instead of calling the (credit-spending) provider API. The cloud worker's GCS
cache-sync relies on those files landing in the configured directory.
"""
import os

import pytest

from karaoke_gen.lyrics_transcriber.transcribers.base_transcriber import BaseTranscriber
from karaoke_gen.lyrics_transcriber.types import TranscriptionData


class FakeTranscriber(BaseTranscriber):
    """Concrete transcriber that counts how often it hits the 'API'."""

    def __init__(self, cache_dir, logger=None):
        super().__init__(cache_dir=cache_dir, logger=logger)
        self.perform_calls = 0

    def get_name(self) -> str:
        return "FakeProvider"

    def _perform_transcription(self, audio_filepath: str):
        self.perform_calls += 1
        # Raw API payload shape mirrors what real providers return before
        # conversion; here it is already the TranscriptionData dict form.
        return {
            "segments": [],
            "words": [],
            "text": "hello world",
            "source": "fakeprovider",
            "metadata": {"audio": os.path.basename(audio_filepath)},
        }

    def _convert_result_format(self, raw_data) -> TranscriptionData:
        return TranscriptionData(
            segments=raw_data.get("segments", []),
            words=raw_data.get("words", []),
            text=raw_data["text"],
            source=raw_data["source"],
            metadata=raw_data.get("metadata", {}),
        )


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "song.wav"
    path.write_bytes(b"deterministic fake audio bytes for md5 hashing")
    return str(path)


def test_first_call_transcribes_and_writes_cache(tmp_path, audio_file):
    cache_dir = tmp_path / "cache"
    t = FakeTranscriber(cache_dir=str(cache_dir))

    result = t.transcribe(audio_file)

    assert result.text == "hello world"
    assert t.perform_calls == 1
    # Both raw and converted cache files land in the configured cache_dir,
    # named "{provider}_{audio_md5}_{suffix}.json".
    written = sorted(p.name for p in cache_dir.glob("fakeprovider_*.json"))
    assert any(name.endswith("_raw.json") for name in written)
    assert any(name.endswith("_converted.json") for name in written)


def test_second_call_hits_converted_cache_without_reperforming(tmp_path, audio_file):
    cache_dir = tmp_path / "cache"
    t = FakeTranscriber(cache_dir=str(cache_dir))

    t.transcribe(audio_file)
    assert t.perform_calls == 1

    # Same audio -> same MD5 -> converted-cache hit -> no second API call.
    result2 = t.transcribe(audio_file)
    assert result2.text == "hello world"
    assert t.perform_calls == 1


def test_raw_cache_hit_when_only_raw_present(tmp_path, audio_file):
    cache_dir = tmp_path / "cache"
    t = FakeTranscriber(cache_dir=str(cache_dir))
    t.transcribe(audio_file)

    # Delete only the converted cache so the raw-cache branch is exercised.
    for p in cache_dir.glob("*_converted.json"):
        p.unlink()

    result = t.transcribe(audio_file)
    assert result.text == "hello world"
    # Still no new API call — served from the raw cache.
    assert t.perform_calls == 1
    # The converted cache is rebuilt from raw.
    assert list(cache_dir.glob("*_converted.json"))


def test_corrupt_cache_file_is_ignored(tmp_path, audio_file):
    cache_dir = tmp_path / "cache"
    t = FakeTranscriber(cache_dir=str(cache_dir))
    t.transcribe(audio_file)

    # Corrupt both cache files -> loader returns None -> re-transcribes.
    for p in cache_dir.glob("fakeprovider_*.json"):
        p.write_text("{ not valid json")

    result = t.transcribe(audio_file)
    assert result.text == "hello world"
    assert t.perform_calls == 2


def test_missing_audio_file_raises(tmp_path):
    t = FakeTranscriber(cache_dir=str(tmp_path / "cache"))
    with pytest.raises(FileNotFoundError):
        t.transcribe(str(tmp_path / "does-not-exist.wav"))
    assert t.perform_calls == 0
