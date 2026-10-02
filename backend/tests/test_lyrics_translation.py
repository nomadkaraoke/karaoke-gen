"""Translated lyrics: LLM translation service + per-job translations.json."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services import lyrics_translation as lt
from backend.services.lyrics_translation import (
    LyricsTranslationError,
    LyricsTranslationService,
    is_same_language,
    normalize_language,
    prepare_job_translations,
)


class FakeStorage:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.deleted = []

    def file_exists(self, path):
        return path in self.files

    def download_json(self, path):
        return self.files[path]

    def upload_json(self, path, data):
        self.files[path] = data

    def delete_file(self, path, ignore_missing=False):
        self.deleted.append(path)
        self.files.pop(path, None)
        return True


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(lt.time, "sleep", lambda s: None)


# ---- language codes ----


@pytest.mark.parametrize("raw,expected", [
    ("es", "es"), ("ES", "es"), ("pt-BR", "pt"), ("zh_CN", "zh"), ("no", "nb"), ("fil", "tl"),
    ("xx", None), ("", None), (None, None),
])
def test_normalize_language(raw, expected):
    assert normalize_language(raw) == expected


def test_supported_languages_match_ui_locales():
    import os

    messages = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "messages")
    locales = {f[:-5] for f in os.listdir(messages) if f.endswith(".json") and not f.startswith(".")}
    assert set(lt.TRANSLATION_LANGUAGES) == locales


def test_is_same_language():
    lines = ["Hola amigo", "Te quiero", "La la la", "Adiós"]
    assert is_same_language(lines, ["hola  amigo", "Te quiero", "La la la", "Adiós"])
    assert not is_same_language(lines, ["Hi friend", "I love you", "La la la", "Bye"])
    assert not is_same_language([], [])


# ---- translate_lines ----


def make_service(responses, storage=None):
    svc = LyricsTranslationService(storage=storage or FakeStorage(), model="test-model")
    svc._call_gemini = MagicMock(side_effect=responses)
    return svc


def test_translate_lines_returns_one_per_line_and_caches():
    storage = FakeStorage()
    svc = make_service([{"translations": ["Hola  mundo", "Adiós"]}], storage)
    assert svc.translate_lines(["Hello world", "Goodbye"], "es") == ["Hola mundo", "Adiós"]
    cached = [p for p in storage.files if p.startswith(lt.CACHE_PREFIX)]
    assert len(cached) == 1
    # Second call is served from the cache
    svc2 = make_service([AssertionError("should not call the model")], storage)
    assert svc2.translate_lines(["Hello world", "Goodbye"], "es") == ["Hola mundo", "Adiós"]


def test_cache_key_depends_on_language_and_lines():
    svc = make_service([])
    a = svc._cache_path(["x"], "es", None, None)
    assert a != svc._cache_path(["x"], "fr", None, None)
    assert a != svc._cache_path(["y"], "es", None, None)


def test_wrong_count_is_retried():
    svc = make_service([{"translations": ["only one"]}, {"translations": ["uno", "dos"]}])
    assert svc.translate_lines(["one", "two"], "es") == ["uno", "dos"]
    assert svc._call_gemini.call_count == 2


def test_gives_up_after_max_attempts():
    svc = make_service([{"translations": []}] * lt.MAX_ATTEMPTS)
    with pytest.raises(LyricsTranslationError):
        svc.translate_lines(["one", "two"], "es")
    assert svc._call_gemini.call_count == lt.MAX_ATTEMPTS


def test_transient_error_retried_permanent_error_raised():
    # Per-minute rate limit: transient, retried.
    transient = Exception("429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerMinutePerProjectPerModel")
    svc = make_service([transient, {"translations": ["uno"]}])
    assert svc.translate_lines(["one"], "es") == ["uno"]

    svc = make_service([Exception("400 invalid argument")])
    with pytest.raises(LyricsTranslationError):
        svc.translate_lines(["one"], "es")
    assert svc._call_gemini.call_count == 1


def test_quota_exhausted_not_retried_and_alerts():
    class QuotaError(Exception):
        code = 403
        status = "PERMISSION_DENIED"

    svc = make_service([QuotaError("Your prepayment credits are depleted")])
    with patch("backend.services.gemini_client._maybe_alert") as alert:
        with pytest.raises(LyricsTranslationError, match="unavailable"):
            svc.translate_lines(["one"], "es")
    assert svc._call_gemini.call_count == 1
    alert.assert_called_once()


def test_unsupported_language_raises():
    with pytest.raises(LyricsTranslationError):
        make_service([]).translate_lines(["one"], "xx")


def test_prompt_lists_numbered_lines_and_language():
    prompt = lt._user_prompt(["first", "second"], "he", "Artist", "Song")
    assert "Hebrew" in prompt and "1. first\n2. second" in prompt and "exactly 2 strings" in prompt


# ---- prepare_job_translations ----


def corrections(*texts):
    segs = []
    for i, text in enumerate(texts):
        words = [{"id": f"w{i}{j}", "text": w, "start_time": i * 3 + j * 0.5, "end_time": i * 3 + j * 0.5 + 0.4}
                 for j, w in enumerate(text.split())]
        segs.append({"id": f"s{i}", "text": text, "words": words,
                     "start_time": words[0]["start_time"] if words else i * 3,
                     "end_time": words[-1]["end_time"] if words else i * 3})
    return {
        "original_segments": segs, "corrected_segments": segs, "corrections": [], "corrections_made": 0,
        "confidence": 1.0, "reference_lyrics": {}, "anchor_sequences": [], "gap_sequences": [],
        "resized_segments": [], "metadata": {}, "correction_steps": [], "word_id_map": {}, "segment_id_map": {},
    }


def job(language="es", job_id="job1"):
    return SimpleNamespace(job_id=job_id, translation_language=language, artist="A", title="T")


def test_no_language_removes_stale_file_and_returns_none():
    storage = FakeStorage({"jobs/job1/lyrics/translations.json": {"lines": []}})
    assert prepare_job_translations("job1", job(None), storage) is None
    assert "jobs/job1/lyrics/translations.json" in storage.deleted


def test_translation_turned_off_clears_previous_outcome():
    # A re-render after an admin removes the language must not keep the old
    # "translated" outcome, or YouTube would still be labelled with a translation.
    from backend.services.youtube_description import translated_language_name

    j = job(None)
    j.state_data = {"lyrics_translation": {"language": "es", "status": "translated"}}
    jm = MagicMock()
    assert prepare_job_translations("job1", j, FakeStorage({}), jm) is None
    jm.update_state_data.assert_called_once_with("job1", "lyrics_translation", {"status": "off"})
    assert translated_language_name({"lyrics_translation": {"status": "off"}}) is None


def test_translates_final_reviewed_lyrics():
    storage = FakeStorage({
        "jobs/job1/lyrics/corrections.json": corrections("Hello world", "Goodbye"),
        # Review edited the first line: the translation must use the edited text
        "jobs/job1/lyrics/corrections_updated.json": corrections("Hello there world", "Goodbye"),
    })
    svc = make_service([{"translations": ["Hola mundo", "Adiós"]}], storage)
    jm = MagicMock()
    path = prepare_job_translations("job1", job(), storage, jm, service=svc)
    assert path == "jobs/job1/lyrics/translations.json"
    data = storage.files[path]
    assert data["language"] == "es" and data["language_name"] == "Spanish"
    assert data["lines"][0] == {"segment_id": "s0", "text": "Hello there world", "translation": "Hola mundo"}
    sent = svc._call_gemini.call_args[0][0]
    assert "Hello there world" in sent
    jm.update_state_data.assert_called_once()
    assert jm.update_state_data.call_args[0][2]["status"] == "translated"


def test_same_language_song_gets_no_translation_row():
    storage = FakeStorage({"jobs/job1/lyrics/corrections.json": corrections("Hola amigo", "Te quiero")})
    svc = make_service([{"translations": ["Hola amigo", "Te quiero"]}], storage)
    jm = MagicMock()
    assert prepare_job_translations("job1", job(), storage, jm, service=svc) is None
    assert jm.update_state_data.call_args[0][2]["status"] == "same_language"
    assert "jobs/job1/lyrics/translations.json" not in storage.files


def test_failure_never_blocks_render():
    storage = FakeStorage({
        "jobs/job1/lyrics/corrections.json": corrections("Hello"),
        "jobs/job1/lyrics/translations.json": {"lines": []},  # stale from an earlier render
    })
    svc = make_service([Exception("400 bad request")], storage)
    jm = MagicMock()
    assert prepare_job_translations("job1", job(), storage, jm, service=svc) is None
    status = jm.update_state_data.call_args[0][2]
    assert status["status"] == "failed" and "bad request" in status["error"]
    assert "jobs/job1/lyrics/translations.json" not in storage.files


# ---- job model ----


def test_job_create_normalizes_translation_language():
    from backend.models.job import JobCreate

    assert JobCreate(artist="a", title="b", translation_language="ES").translation_language == "es"
    assert JobCreate(artist="a", title="b", translation_language="").translation_language is None
    assert JobCreate(artist="a", title="b", translation_language="klingon").translation_language is None
    assert JobCreate(artist="a", title="b").translation_language is None


def test_job_manager_copies_translation_language():
    from backend.models.job import JobCreate
    from backend.services.job_manager import JobManager

    with patch("backend.services.job_manager.FirestoreService"), patch("backend.services.job_manager.StorageService"):
        manager = JobManager()
    manager.firestore = MagicMock()
    created = manager.create_job(JobCreate(artist="a", title="b", theme_id="nomad", translation_language="ja"))
    assert created.translation_language == "ja"
    saved = manager.firestore.create_job.call_args[0][0]
    assert saved.translation_language == "ja"


# ---- preview ----


def test_preview_samples_cover_every_language():
    from backend.services.translation_preview_samples import SAMPLES

    assert set(SAMPLES) == set(lt.TRANSLATION_LANGUAGES)
    for code, sample in SAMPLES.items():
        assert len(sample["lyrics"]) == len(sample["translations"]) == 4, code
        assert all(t.strip() for t in sample["translations"]), code


def test_preview_route_rejects_unknown_language():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes.themes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    assert client.get("/api/themes/translation-preview?language=xx").status_code == 400
    with patch("backend.services.translation_preview.render_translation_preview", return_value="data:image/jpeg;base64,AA"):
        resp = client.get("/api/themes/translation-preview?language=he")
    assert resp.status_code == 200 and resp.json() == {"image": "data:image/jpeg;base64,AA"}


def test_preview_renders_sample_with_translations():
    from backend.services import translation_preview as tp

    tp._CACHE.clear()
    theme_service = MagicMock()
    theme_service.get_default_theme_id.return_value = "nomad"
    theme_service.get_theme_style_params.return_value = {"karaoke": {}}
    with patch.object(tp, "get_theme_service", return_value=theme_service), \
         patch.object(tp, "resolve_assets", side_effect=lambda s, t: s), \
         patch.object(tp, "render_karaoke_frame", return_value=b"jpeg") as render:
        url = tp.render_translation_preview("fr")
        tp.render_translation_preview("fr")  # cached
    assert url.startswith("data:image/jpeg;base64,")
    render.assert_called_once()
    assert render.call_args.kwargs["translations"] == tp.SAMPLES["fr"]["translations"]


def test_concurrent_cold_previews_render_once():
    import threading
    import time as _time

    from backend.services import translation_preview as tp

    tp._CACHE.clear()
    theme_service = MagicMock()
    theme_service.get_default_theme_id.return_value = "nomad"
    theme_service.get_theme_style_params.return_value = {"karaoke": {}}

    def slow_render(*a, **k):
        _time.sleep(0.2)
        return b"jpeg"

    with patch.object(tp, "get_theme_service", return_value=theme_service), \
         patch.object(tp, "resolve_assets", side_effect=lambda s, t: s), \
         patch.object(tp, "render_karaoke_frame", side_effect=slow_render) as render:
        threads = [threading.Thread(target=tp.render_translation_preview, args=("de",)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert render.call_count == 1
