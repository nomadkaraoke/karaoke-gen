"""
Tests for the storage-retention purge (backend/services/storage_retention.py).

Covers the per-file policy (keep list, purge list, 720p keep, user
instrumentals, finalise-only sources, unknown files), job eligibility
(age, job types, in-flight markers, tenants), per-job planning (input must be
recoverable, bring-your-own-instrumental keeps stems, never outside
jobs/{id}/), file_urls cleanup, and the service: dry-run writes only a report,
real runs delete + record markers/manifest, scoping, batching + cursor,
idempotency and the eligibility re-check at claim time.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.cloud.firestore_v1 import DELETE_FIELD

from backend.services import storage_retention as sr
from backend.services.storage_retention import (
    KEEP,
    PURGE,
    StorageRetentionService,
    classify_file,
    completed_at,
    file_url_deletions,
    job_skip_reason,
    plan_job,
    purge_in_progress,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=45)).isoformat()


# --- classify_file ----------------------------------------------------------------

class TestClassifyFile:
    @pytest.mark.parametrize("rel", [
        "input/Artist - Title.flac",
        "input/edited.flac",
        "lyrics/corrections.json",
        "lyrics/karaoke.lrc",
        "style/style_params.json",
        "review_sessions/abc.json",
        "audio_edit/session1/stack.json",
        "audio_edit_sessions/x.json",
        "uploads/custom_instrumental_source.wav",
        "packages/cdg_zip.zip",
        "packages/txt_zip.zip",
        "screens/title.png",
        "screens/end.jpg",
        "analysis/backing_vocals_waveform.png",
        "stems/custom_instrumental.flac",
        "stems/custom_instrumental.mp3",
        "stems/vocals_derived.flac",
        "custom_instrumental.mp3",
        "custom_instrumental.wav",
        "existing_instrumental.flac",
        "quick/lyrics.json",
        "something_new/file.bin",
        "random_root_file.txt",
    ])
    def test_kept(self, rel):
        assert classify_file(rel)[0] == KEEP

    @pytest.mark.parametrize("rel, category", [
        ("finals/lossless_4k_mp4.mp4", "finals"),
        ("finals/lossless_4k_mkv.mkv", "finals"),
        ("finals/lossy_4k_mp4.mp4", "finals"),
        ("finals/with_vocals_mp4.mp4", "finals"),
        ("finals/portrait_1080x1920.mp4", "finals"),
        ("finals/title_mov.mov", "finals"),
        ("finals/Artist - Title (Final Karaoke Lossless 4k).mp4", "finals"),
        ("finals/Artist - Title (Final Karaoke Lossy 4k).mp4", "finals"),
        ("finals/Artist - Title (Karaoke).mp4", "finals"),
        ("finals/Artist - Title (With Vocals).mp4", "finals"),
        ("finals/Artist - Title (Title).mov", "finals"),
        ("videos/with_vocals.mkv", "videos"),
        ("previews/f9d280ecd095.mp4", "previews"),
        ("previews/f9d280ecd095.ass", "previews"),
        ("encoded/out.mp4", "encoded"),
        ("quick/video.mp4", "quick"),
        ("screens/title.mov", "screens_mov"),
        ("screens/end.mov", "screens_mov"),
        ("review-audio/instrumental_clean.ogg", "review_audio"),
        ("review-audio/waveform_review_500.json", "review_audio"),
        ("stems/instrumental_clean.flac", "stems"),
        ("stems/instrumental_with_backing.flac", "stems"),
        ("stems/vocals_clean.flac", "stems"),
        ("stems/lead_vocals.flac", "stems"),
        ("stems/backing_vocals.flac", "stems"),
    ])
    def test_purged(self, rel, category):
        assert classify_file(rel) == (PURGE, category)

    @pytest.mark.parametrize("rel", [
        "finals/lossy_720p_mp4.mp4",
        "finals/Artist - Title (Final Karaoke Lossy 720p).mp4",
        # artist/title containing format words must not confuse the 720p keep
        "finals/Nat King Cole - Portrait of Jennie (Final Karaoke Lossy 720p).mp4",
    ])
    def test_720p_final_is_kept(self, rel):
        assert classify_file(rel) == (KEEP, "final_720p")

    def test_720p_lookalike_title_is_still_purged(self):
        # "720p" in the title but the format tag says 4K
        assert classify_file("finals/720p Song - X (Final Karaoke Lossy 4k).mp4")[0] == PURGE

    @pytest.mark.parametrize("rel", [
        "videos/with_vocals.mkv",
        "videos/with_vocals.mov",
        "screens/title.mov",
        "screens/end.mov",
        "stems/instrumental_clean.flac",
        "stems/instrumental_with_backing.flac",
    ])
    def test_finalise_only_sources_are_kept(self, rel):
        assert classify_file(rel, finalise_only=True)[0] == KEEP

    def test_finalise_only_still_purges_regenerable_finals(self):
        assert classify_file("finals/lossy_4k_mp4.mp4", finalise_only=True)[0] == PURGE

    def test_unrecoverable_stems_are_kept(self):
        assert classify_file("stems/instrumental_clean.flac", stems_purgeable=False) == (KEEP, "stems_unrecoverable")


# --- completed_at / eligibility ---------------------------------------------------

def _job(**overrides):
    job = {
        "job_id": "job1",
        "status": "complete",
        "timeline": [
            {"status": "encoding", "timestamp": (NOW - timedelta(days=46)).isoformat()},
            {"status": "complete", "timestamp": OLD},
        ],
        "input_media_gcs_path": "jobs/job1/input/song.flac",
        "state_data": {"instrumental_selection": "clean"},
        "file_urls": {"lyrics": {"corrections": "jobs/job1/lyrics/corrections.json"}},
        "theme_id": "nomad",
        "tenant_id": "",
    }
    job.update(overrides)
    return job


class TestEligibility:
    def test_completed_at_uses_latest_complete_entry(self):
        later = (NOW - timedelta(days=3)).isoformat()
        job = _job(timeline=[{"status": "complete", "timestamp": OLD},
                             {"status": "complete", "timestamp": later}])
        assert completed_at(job) == datetime.fromisoformat(later)

    def test_completed_at_falls_back_to_updated_at(self):
        assert completed_at(_job(timeline=[], updated_at=NOW)) == NOW

    def test_naive_timestamps_are_utc(self):
        assert completed_at(_job(timeline=[{"status": "complete", "timestamp": "2026-08-01T10:00:00"}])).tzinfo

    def test_old_complete_job_is_candidate(self):
        assert job_skip_reason(_job(), NOW, 30) is None

    @pytest.mark.parametrize("overrides, reason", [
        ({"status": "failed"}, "not_complete"),
        ({"status": "in_review"}, "not_complete"),
        ({"finalise_only": True}, "finalise_only"),
        ({"prep_only": True}, "prep_only"),
        ({"outputs_deleted_at": NOW}, "outputs_deleted"),
        ({"state_data": {"visibility_change_in_progress": True}}, "active_visibility_change_in_progress"),
        ({"state_data": {"admin_rerender": {"brand_code": "X"}}}, "active_admin_rerender"),
        ({"state_data": {"theme_rerender": {"theme_id": "t"}}}, "active_theme_rerender"),
        ({"state_data": {"regenerate": {"source": "customer"}}}, "active_regenerate"),
        ({"state_data": {"youtube_upload_queued": True}}, "youtube_upload_queued"),
        ({"state_data": {"storage_purge_in_progress": NOW.isoformat()}}, "purge_in_progress"),
        ({"timeline": [{"status": "complete", "timestamp": (NOW - timedelta(days=29)).isoformat()}]}, "too_recent"),
        ({"timeline": [], "updated_at": None}, "no_completion_time"),
        # only purge what a regenerate could rebuild
        ({"state_data": {}}, "not_regenerable_no_instrumental_selection"),
        ({"file_urls": {}}, "not_regenerable_no_reviewed_lyrics"),
        ({"theme_id": None}, "not_regenerable_no_theme"),
    ])
    def test_skip_reasons(self, overrides, reason):
        assert job_skip_reason(_job(**overrides), NOW, 30) == reason

    def test_stale_purge_claim_does_not_block(self):
        stale = (NOW - timedelta(hours=2)).isoformat()
        job = _job(state_data={"storage_purge_in_progress": stale, "instrumental_selection": "clean"})
        assert job_skip_reason(job, NOW, 30) is None

    def test_excluded_tenant(self):
        assert job_skip_reason(_job(tenant_id="vocalstar"), NOW, 30, ["vocalstar"]) == "excluded_tenant"

    def test_tenant_jobs_follow_the_same_policy_by_default(self):
        assert job_skip_reason(_job(tenant_id="randy-vild"), NOW, 30) is None

    def test_purge_in_progress_helper_accepts_models_and_dicts(self):
        recent = datetime.now(timezone.utc).isoformat()
        assert purge_in_progress({"state_data": {"storage_purge_in_progress": recent}})
        assert purge_in_progress(SimpleNamespace(state_data={"storage_purge_in_progress": recent}))
        assert not purge_in_progress(SimpleNamespace(state_data={}))
        assert not purge_in_progress(SimpleNamespace(state_data=None))


# --- plan_job -----------------------------------------------------------------------

def _listing(job_id="job1", extra=()):
    files = [
        ("input/song.flac", 30_000_000),
        ("lyrics/corrections.json", 200_000),
        ("finals/lossless_4k_mp4.mp4", 60_000_000),
        ("finals/lossy_4k_mp4.mp4", 30_000_000),
        ("finals/lossy_720p_mp4.mp4", 29_000_000),
        ("finals/Artist - Title (Final Karaoke Lossy 720p).mp4", 29_000_000),
        ("finals/Artist - Title (Karaoke).mp4", 140_000_000),
        ("videos/with_vocals.mkv", 150_000_000),
        ("previews/abc.mp4", 3_000_000),
        ("review-audio/instrumental_clean.ogg", 3_000_000),
        ("screens/title.png", 3_000_000),
        ("screens/title.mov", 300_000),
        ("packages/cdg_zip.zip", 4_000_000),
        ("stems/instrumental_clean.flac", 23_000_000),
        ("stems/vocals_clean.flac", 14_000_000),
        *extra,
    ]
    return [(f"jobs/{job_id}/{rel}", size) for rel, size in files]


class TestPlanJob:
    def test_purges_regenerables_and_keeps_the_rest(self):
        plan = plan_job(_job(), _listing())
        purged = {p.split("/", 2)[2] for p, _, _ in plan.purge}
        assert purged == {
            "finals/lossless_4k_mp4.mp4", "finals/lossy_4k_mp4.mp4",
            "finals/Artist - Title (Karaoke).mp4", "videos/with_vocals.mkv",
            "previews/abc.mp4", "review-audio/instrumental_clean.ogg",
            "screens/title.mov", "stems/instrumental_clean.flac", "stems/vocals_clean.flac",
        }
        assert plan.purges_renders and plan.purges_stems
        assert plan.bytes_by_category()["stems"] == 37_000_000
        assert plan.kept_bytes == 30_000_000 + 200_000 + 29_000_000 * 2 + 3_000_000 + 4_000_000

    def test_custom_instrumental_is_never_purged(self):
        extra = [("stems/custom_instrumental.flac", 40_000_000), ("custom_instrumental.mp3", 9_000_000)]
        plan = plan_job(_job(state_data={"instrumental_selection": "custom"}), _listing(extra=extra))
        purged = {p for p, _, _ in plan.purge}
        assert "jobs/job1/stems/custom_instrumental.flac" not in purged
        assert "jobs/job1/custom_instrumental.mp3" not in purged
        # the separated stems still go (re-separated on demand)
        assert "jobs/job1/stems/instrumental_clean.flac" in purged

    def test_input_unavailable_keeps_everything(self):
        plan = plan_job(_job(input_media_gcs_path="uploads/job1/audio/song.flac"), _listing())
        assert plan.skip_reason == "input_unavailable" and not plan.purge

    def test_input_missing_from_listing_keeps_everything(self):
        listing = [x for x in _listing() if "/input/" not in x[0]]
        assert plan_job(_job(), listing).skip_reason == "input_unavailable"

    def test_input_only_from_input_media_gcs_path(self):
        # regenerate/separation/render read input_media_gcs_path only
        job = _job(input_media_gcs_path=None, file_urls={"input": {"audio": "jobs/job1/input/song.flac"}})
        assert plan_job(job, _listing()).skip_reason == "input_unavailable"

    def test_existing_instrumental_job_keeps_stems(self):
        plan = plan_job(_job(existing_instrumental_gcs_path="jobs/job1/custom_instrumental.flac"), _listing())
        assert not plan.purges_stems and plan.purges_renders
        assert plan.stems_note == "existing_instrumental"

    def test_finalise_only_sources_kept_by_plan(self):
        plan = plan_job(_job(finalise_only=True), _listing())
        purged = {p for p, _, _ in plan.purge}
        assert "jobs/job1/videos/with_vocals.mkv" not in purged
        assert "jobs/job1/screens/title.mov" not in purged
        assert "jobs/job1/stems/instrumental_clean.flac" not in purged

    def test_never_plans_outside_the_job_prefix(self):
        listing = _listing() + [("jobs/job10/finals/lossy_4k_mp4.mp4", 1), ("uploads/job1/x.mp4", 1)]
        plan = plan_job(_job(), listing)
        assert all(p.startswith("jobs/job1/") for p, _, _ in plan.purge)

    def test_tenant_job_planned_like_any_other(self):
        plan = plan_job(_job(tenant_id="randy-vild"), _listing())
        assert plan.purges_renders and plan.purges_stems


class TestFileUrlDeletions:
    def test_only_purged_entries_are_dropped(self):
        file_urls = {
            "finals": {"lossy_4k_mp4": "jobs/job1/finals/lossy_4k_mp4.mp4",
                       "lossy_720p_mp4": "jobs/job1/finals/lossy_720p_mp4.mp4"},
            "stems": {"instrumental_clean": "gs://bucket/jobs/job1/stems/instrumental_clean.flac",
                      "custom_instrumental": "jobs/job1/stems/custom_instrumental.flac"},
            "videos": {"with_vocals": "jobs/job1/videos/with_vocals.mkv"},
            "input": "jobs/job1/input/song.flac",
        }
        purged = ["jobs/job1/finals/lossy_4k_mp4.mp4", "jobs/job1/stems/instrumental_clean.flac",
                  "jobs/job1/videos/with_vocals.mkv"]
        assert sorted(file_url_deletions(file_urls, purged)) == [
            "file_urls.finals.lossy_4k_mp4",
            "file_urls.stems.instrumental_clean",
            "file_urls.videos.with_vocals",
        ]

    def test_unsafe_keys_are_never_written(self):
        file_urls = {"finals": {"weird.key": "jobs/job1/finals/a.mp4"}}
        assert file_url_deletions(file_urls, ["jobs/job1/finals/a.mp4"]) == []


# --- Service ----------------------------------------------------------------------

class FakeStorage:
    def __init__(self, files, updated=None):
        self.files = dict(files)
        self.updated = dict(updated or {})
        self.deleted = []
        self.reports = {}
        self.texts = {}
        self.delete_generations = {}

    def list_files_with_sizes(self, prefix):
        return [(k, v) for k, v in sorted(self.files.items()) if k.startswith(prefix)]

    def list_blob_meta(self, prefix):
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [{"name": k, "size": v, "generation": 1000 + i, "updated": self.updated.get(k, old)}
                for i, (k, v) in enumerate(sorted(self.files.items())) if k.startswith(prefix)]

    def delete_file(self, path, ignore_missing=False, if_generation_match=None):
        self.deleted.append(path)
        self.delete_generations[path] = if_generation_match
        self.files.pop(path, None)
        return True

    def upload_json(self, path, data):
        self.reports[path] = data
        return path

    def upload_text(self, path, text, content_type="text/plain"):
        self.texts[path] = text
        return path


class FakeDoc:
    def __init__(self, db, doc_id):
        self.db, self.id = db, doc_id

    def get(self, transaction=None):
        data = self.db.data.get(self.id)
        return SimpleNamespace(exists=data is not None, id=self.id, to_dict=lambda: _copy(data))

    def update(self, payload):
        self.db.updates.append((self.id, payload))
        _apply(self.db.data.setdefault(self.id, {}), payload)

    def set(self, payload, merge=False):
        self.db.data.setdefault(self.id, {}).update(payload)


def _copy(data):
    import copy
    return copy.deepcopy(data) if data is not None else None


def _apply(doc, payload):
    import sys
    # The service imports DELETE_FIELD at call time; other suites stub
    # google.cloud.firestore_v1, so compare against whatever is live now.
    delete_sentinels = {id(DELETE_FIELD), id(getattr(sys.modules.get("google.cloud.firestore_v1"), "DELETE_FIELD", DELETE_FIELD))}
    for key, value in payload.items():
        parts = key.split(".")
        target = doc
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        if id(value) in delete_sentinels:
            target.pop(parts[-1], None)
        else:
            target[parts[-1]] = value


class FakeQuery:
    def __init__(self, db, docs):
        self.db, self.docs = db, docs
        self._limit, self._after = None, None

    def where(self, filter=None):
        return FakeQuery(self.db, [d for d in self.docs if self.db.data[d].get("status") == "complete"])

    def order_by(self, *_):
        q = FakeQuery(self.db, sorted(self.docs)); q._limit, q._after = self._limit, self._after
        return q

    def select(self, _):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def start_after(self, cursor):
        self._after = cursor["__name__"].id
        return self

    def stream(self):
        docs = [d for d in self.docs if self._after is None or d > self._after]
        if self._limit:
            docs = docs[: self._limit]
        return [SimpleNamespace(id=d, to_dict=(lambda d=d: _copy(self.db.data[d]))) for d in docs]


class FakeCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def document(self, doc_id):
        if self.name == "storage_retention":
            return self.db.state_doc
        return FakeDoc(self.db, doc_id)

    def where(self, filter=None):
        return FakeQuery(self.db, list(self.db.data)).where(filter)

    def select(self, fields):
        return FakeQuery(self.db, list(self.db.data))


class FakeDb:
    def __init__(self, jobs):
        self.data = {j["job_id"]: j for j in jobs}
        self.updates = []
        self.state = {}
        state = self

        class StateDoc:
            def get(self_inner):
                return SimpleNamespace(exists=bool(state.state), to_dict=lambda: dict(state.state))

            def set(self_inner, payload, merge=False):
                state.state.update(payload)

        self.state_doc = StateDoc()

    def collection(self, name):
        return FakeCollection(self, name)

    def transaction(self):
        class FakeTransaction:
            def update(self_inner, ref, payload):
                ref.update(payload)

        return FakeTransaction()


def _settings(**overrides):
    values = dict(
        storage_retention_dry_run=True, storage_retention_min_age_days=30,
        storage_retention_max_jobs_per_run=50, storage_retention_excluded_tenants="",
        firestore_collection="jobs", gcs_bucket_name="bucket",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _no_transactions():
    with patch.object(sr, "_run_in_transaction", lambda db, fn: fn(db.transaction())), \
         patch("backend.services.firestore_service.log_to_job"):
        yield


def _service(jobs, files=None, **settings):
    files = files if files is not None else {k: v for j in jobs for k, v in _listing(j["job_id"])}
    for j in jobs:
        j.setdefault("input_media_gcs_path", f"jobs/{j['job_id']}/input/song.flac")
        j["input_media_gcs_path"] = f"jobs/{j['job_id']}/input/song.flac"
    db, storage = FakeDb(jobs), FakeStorage(files)
    return StorageRetentionService(db=db, storage=storage, settings=_settings(**settings)), db, storage


class TestServiceDryRun:
    def test_dry_run_reports_without_touching_anything(self):
        svc, db, storage = _service([_job(job_id="a"), _job(job_id="b", status="failed")])
        report = svc.run(now=NOW)
        assert report["dry_run"] is True
        assert storage.deleted == [] and db.updates == []
        assert [j["job_id"] for j in report["jobs"]] == ["a"]
        assert report["summary"]["jobs_to_purge"] == 1
        cats = report["summary"]["bytes_by_category"]
        assert cats["finals"] == 60_000_000 + 30_000_000 + 140_000_000
        assert cats["stems"] == 37_000_000
        assert "final_720p" not in cats
        # only completed jobs are walked at all
        assert report["skipped"] == {} and report["summary"]["jobs_scanned"] == 1
        # report written to GCS
        assert report["report_path"] in storage.reports
        assert report["report_path"].startswith("storage-retention/reports/") and "dry-run" in report["report_path"]

    def test_default_comes_from_settings(self):
        svc, db, storage = _service([_job(job_id="a")], storage_retention_dry_run=True)
        assert svc.run(now=NOW)["dry_run"] is True

    def test_dry_run_scans_everything_without_cursor(self):
        jobs = [_job(job_id=f"j{i:03d}") for i in range(5)]
        svc, db, storage = _service(jobs)
        report = svc.run(now=NOW)
        assert report["summary"]["jobs_to_purge"] == 5
        assert db.state == {}  # cursor untouched


class TestServiceRealRun:
    def test_real_run_deletes_and_records(self):
        job = _job(job_id="a", file_urls={
            "lyrics": {"corrections": "jobs/a/lyrics/corrections.json"},
            "finals": {"lossy_4k_mp4": "jobs/a/finals/lossy_4k_mp4.mp4",
                       "lossy_720p_mp4": "jobs/a/finals/lossy_720p_mp4.mp4"},
            "stems": {"instrumental_clean": "jobs/a/stems/instrumental_clean.flac"},
            "videos": {"with_vocals": "jobs/a/videos/with_vocals.mkv"},
            "packages": {"cdg_zip": "jobs/a/packages/cdg_zip.zip"},
        })
        svc, db, storage = _service([job])
        report = svc.run(dry_run=False, now=NOW)

        assert report["summary"]["jobs_purged"] == 1
        assert "jobs/a/finals/lossy_720p_mp4.mp4" not in storage.deleted
        assert "jobs/a/input/song.flac" not in storage.deleted
        assert "jobs/a/packages/cdg_zip.zip" not in storage.deleted
        assert "jobs/a/stems/instrumental_clean.flac" in storage.deleted
        doc = db.data["a"]
        assert doc["renders_purged_at"] == NOW and doc["stems_purged_at"] == NOW
        assert doc["file_urls"]["finals"] == {"lossy_720p_mp4": "jobs/a/finals/lossy_720p_mp4.mp4"}
        assert "instrumental_clean" not in doc["file_urls"]["stems"]
        assert "with_vocals" not in doc["file_urls"]["videos"]
        assert doc["file_urls"]["packages"] == {"cdg_zip": "jobs/a/packages/cdg_zip.zip"}
        manifest = doc["storage_purge"]
        assert manifest["status"] == "complete"
        assert manifest["bytes"] == sum(f["bytes"] for f in manifest["files"])
        assert {f["path"] for f in manifest["files"]} == set(storage.deleted)
        # the claim marker is cleared
        assert "storage_purge_in_progress" not in doc["state_data"]
        assert "run" in report["report_path"] and "dry-run" not in report["report_path"]

    def test_second_run_is_idempotent(self):
        svc, db, storage = _service([_job(job_id="a")])
        svc.run(dry_run=False, now=NOW)
        deleted = list(storage.deleted)
        report = svc.run(dry_run=False, now=NOW + timedelta(days=1))
        assert storage.deleted == deleted
        assert report["skipped"] == {"already_purged": 1}

    def test_regenerated_job_becomes_eligible_again_after_its_window(self):
        svc, db, storage = _service([_job(job_id="a")])
        svc.run(dry_run=False, now=NOW)
        # regenerate: files come back, a new completion is recorded
        regenerated_at = NOW + timedelta(days=1)
        db.data["a"]["timeline"].append({"status": "complete", "timestamp": regenerated_at.isoformat()})
        db.data["a"]["renders_purged_at"] = None
        db.data["a"]["stems_purged_at"] = None
        storage.files.update({k: v for k, v in _listing("a")})
        assert svc.run(dry_run=True, now=NOW + timedelta(days=10))["skipped"] == {"too_recent": 1}
        later = svc.run(dry_run=True, now=regenerated_at + timedelta(days=31))
        assert later["summary"]["jobs_to_purge"] == 1

    def test_custom_instrumental_job(self):
        files = {k: v for k, v in _listing("a", extra=[("stems/custom_instrumental.flac", 1),
                                                         ("custom_instrumental.mp3", 1)])}
        svc, db, storage = _service([_job(job_id="a", state_data={"instrumental_selection": "custom"})], files)
        svc.run(dry_run=False, now=NOW)
        assert "jobs/a/stems/custom_instrumental.flac" in storage.files
        assert "jobs/a/custom_instrumental.mp3" in storage.files
        assert "jobs/a/finals/lossy_720p_mp4.mp4" in storage.files

    def test_finalise_only_job_untouched(self):
        svc, db, storage = _service([_job(job_id="a", finalise_only=True)])
        report = svc.run(dry_run=False, now=NOW)
        assert storage.deleted == [] and report["skipped"] == {"finalise_only": 1}

    def test_tenant_job_purged_unless_excluded(self):
        svc, db, storage = _service([_job(job_id="a", tenant_id="vocalstar")])
        assert svc.run(dry_run=True, now=NOW)["summary"]["jobs_to_purge"] == 1
        svc, db, storage = _service([_job(job_id="a", tenant_id="vocalstar")],
                                    storage_retention_excluded_tenants="vocalstar, singa")
        assert svc.run(dry_run=False, now=NOW)["skipped"] == {"excluded_tenant": 1}
        assert storage.deleted == []

    def test_claim_rechecks_live_doc(self):
        svc, db, storage = _service([_job(job_id="a")])
        original_plan = sr.plan_job

        def plan_then_race(job, listing):
            # a regenerate claims the job between planning and deletion
            db.data["a"]["state_data"]["regenerate"] = {"source": "customer"}
            return original_plan(job, listing)

        with patch.object(sr, "plan_job", side_effect=plan_then_race):
            report = svc.run(dry_run=False, now=NOW)
        assert storage.deleted == []
        assert report["jobs"][0]["result"] == {"status": "skipped", "reason": "no_longer_eligible"}
        assert report["summary"]["jobs_purged"] == 0

    def test_delete_failures_are_recorded_not_fatal(self):
        svc, db, storage = _service([_job(job_id="a")])
        real_delete = storage.delete_file

        def flaky(path, ignore_missing=False, if_generation_match=None):
            if path.endswith("lossy_4k_mp4.mp4"):
                raise RuntimeError("boom")
            return real_delete(path, ignore_missing, if_generation_match)

        storage.delete_file = flaky
        svc.run(dry_run=False, now=NOW)
        manifest = db.data["a"]["storage_purge"]
        assert manifest["failures"][0]["path"].endswith("lossy_4k_mp4.mp4")
        assert db.data["a"]["renders_purged_at"] == NOW

    def test_batch_limit_and_resumable_cursor(self):
        jobs = [_job(job_id=f"j{i}") for i in range(5)]
        svc, db, storage = _service(jobs)
        first = svc.run(dry_run=False, max_jobs=2, now=NOW)
        assert first["summary"]["jobs_purged"] == 2
        assert db.state["cursor_job_id"] == "j1"
        second = svc.run(dry_run=False, max_jobs=2, now=NOW)
        assert [j["job_id"] for j in second["jobs"]] == ["j2", "j3"]
        third = svc.run(dry_run=False, max_jobs=2, now=NOW)
        assert [j["job_id"] for j in third["jobs"]] == ["j4"]
        assert db.state["cursor_job_id"] is None  # wrapped

    def test_real_run_uses_settings_batch_size(self):
        jobs = [_job(job_id=f"j{i}") for i in range(3)]
        svc, db, storage = _service(jobs, storage_retention_max_jobs_per_run=1)
        assert svc.run(dry_run=False, now=NOW)["summary"]["jobs_purged"] == 1

    def test_scoped_run_only_touches_listed_jobs(self):
        svc, db, storage = _service([_job(job_id="a"), _job(job_id="b")])
        report = svc.run(dry_run=False, job_ids=["b"], now=NOW)
        assert [j["job_id"] for j in report["jobs"]] == ["b"]
        assert all(p.startswith("jobs/b/") for p in storage.deleted)
        assert db.state == {}

    def test_scoped_min_age_override(self):
        recent = _job(job_id="a", timeline=[{"status": "complete", "timestamp": NOW.isoformat()}])
        svc, db, storage = _service([recent])
        assert svc.run(dry_run=True, job_ids=["a"], now=NOW)["skipped"] == {"too_recent": 1}
        assert svc.run(dry_run=True, job_ids=["a"], min_age_days=0, now=NOW)["summary"]["jobs_to_purge"] == 1

    def test_orphans_section(self):
        files = {k: v for k, v in _listing("a")}
        files["jobs/ghost/finals/x.mp4"] = 2**30
        svc, db, storage = _service([_job(job_id="a")], files)
        report = svc.run(dry_run=True, include_orphans=True, now=NOW)
        assert report["orphans"]["folders"] == 1
        assert report["orphans"]["largest"][0]["job_id"] == "ghost"


class TestInterruptedPurge:
    def test_markers_are_set_before_any_delete(self):
        svc, db, storage = _service([_job(job_id="a")])
        seen = {}

        def delete(path, ignore_missing=False, if_generation_match=None):
            # at the first delete the job must already say what's being purged
            if not seen:
                doc = db.data["a"]
                seen.update(renders=doc.get("renders_purged_at"), stems=doc.get("stems_purged_at"),
                            status=(doc.get("storage_purge") or {}).get("status"))
            storage.files.pop(path, None)
            storage.deleted.append(path)
            return True

        storage.delete_file = delete
        svc.run(dry_run=False, now=NOW)
        assert seen == {"renders": NOW, "stems": NOW, "status": "pending"}

    def test_interrupted_purge_is_finished_by_next_pass(self):
        svc, db, storage = _service([_job(job_id="a")])
        calls = {"n": 0}
        real = storage.delete_file

        def dies_after_two(path, ignore_missing=False, if_generation_match=None):
            calls["n"] += 1
            if calls["n"] > 2:
                raise SystemExit("instance killed")
            return real(path, ignore_missing, if_generation_match)

        storage.delete_file = dies_after_two
        with pytest.raises(SystemExit):
            svc.run(dry_run=False, now=NOW)
        doc = db.data["a"]
        assert doc["storage_purge"]["status"] == "pending"
        assert doc["stems_purged_at"] == NOW  # regenerate will re-separate
        storage.delete_file = real
        db.data["a"]["state_data"].pop("storage_purge_in_progress", None)  # stale claim
        report = svc.run(dry_run=False, now=NOW + timedelta(hours=1))
        assert report["summary"]["jobs_purged"] == 1
        assert db.data["a"]["storage_purge"]["status"] == "complete"



# --- Deletion audit log -------------------------------------------------------------

import json as _json


def _log_lines(storage, path):
    return [_json.loads(line) for line in storage.texts[path].splitlines() if line]


class TestDeletionLog:
    def test_real_run_logs_every_deleted_object_with_generation(self):
        svc, db, storage = _service([_job(job_id="a")])
        report = svc.run(dry_run=False, now=NOW)
        log_path = report["deletion_log"]
        assert log_path == "storage-retention/deletion-logs/20261003T120000Z-job_purge.jsonl"
        lines = _log_lines(storage, log_path)
        assert {l["path"] for l in lines} == set(storage.deleted)
        assert all(l["result"] == "deleted" and l["mode"] == "job_purge" and l["job_id"] == "a" for l in lines)
        assert all(l["generation"] is not None for l in lines)
        cats = {l["category"] for l in lines}
        assert {"finals", "videos", "stems", "review-audio", "previews", "screens-mov"} == cats
        # deletes are generation-pinned to exactly the logged version
        for l in lines:
            assert str(storage.delete_generations[l["path"]]) == l["generation"]
        summary = storage.reports[report["deletion_summary"]]
        assert summary["objects_deleted"] == len(lines)
        assert summary["bytes_deleted"] == sum(l["size_bytes"] for l in lines)
        assert summary["jobs"] == 1 and summary["failures"] == 0
        # manifest on the job doc carries generations + log path
        manifest = db.data["a"]["storage_purge"]
        assert manifest["deletion_log"] == log_path
        assert all(f["generation"] for f in manifest["files"])

    def test_dry_run_writes_no_deletion_log(self):
        svc, db, storage = _service([_job(job_id="a")])
        report = svc.run(dry_run=True, now=NOW)
        assert "deletion_log" not in report and storage.texts == {}

    def test_failures_are_logged(self):
        svc, db, storage = _service([_job(job_id="a")])
        real = storage.delete_file

        def flaky(path, ignore_missing=False, if_generation_match=None):
            if path.endswith("lossy_4k_mp4.mp4"):
                raise RuntimeError("412 precondition failed")
            return real(path, ignore_missing, if_generation_match)

        storage.delete_file = flaky
        report = svc.run(dry_run=False, now=NOW)
        failed = [l for l in _log_lines(storage, report["deletion_log"]) if l["result"] == "failed"]
        assert len(failed) == 1 and "precondition" in failed[0]["error"]
        assert storage.reports[report["deletion_summary"]]["failures"] == 1

    def test_log_is_flushed_per_job_so_a_crash_keeps_the_record(self):
        jobs = [_job(job_id="a"), _job(job_id="b")]
        svc, db, storage = _service(jobs)
        real = storage.delete_file

        def die_on_b(path, ignore_missing=False, if_generation_match=None):
            if path.startswith("jobs/b/"):
                raise SystemExit("killed")
            return real(path, ignore_missing, if_generation_match)

        storage.delete_file = die_on_b
        with pytest.raises(SystemExit):
            svc.run(dry_run=False, now=NOW)
        path = "storage-retention/deletion-logs/20261003T120000Z-job_purge.jsonl"
        lines = _log_lines(storage, path)
        assert lines and {l["job_id"] for l in lines} == {"a"}
        assert {l["path"] for l in lines} == {p for p in storage.deleted if p.startswith("jobs/a/")}

    @pytest.mark.parametrize("path", [
        "storage-retention/deletion-logs/x.jsonl", "uploads/a/x.flac", "jobs/", "jobs//x", "temp/x",
    ])
    def test_guard_refuses_paths_outside_job_folders(self, path):
        with pytest.raises(ValueError):
            sr.assert_deletable(path)

    def test_guard_allows_job_files(self):
        sr.assert_deletable("jobs/a/finals/x.mp4")


# --- Orphan folders ----------------------------------------------------------------

def _orphan_service(files, updated=None, records=None):
    db = FakeDb([_job(job_id="live")])
    for coll, ids in (records or {}).items():
        db.extra = getattr(db, "extra", {})
        db.extra.update({(coll, i): True for i in ids})

    real_collection = db.collection

    def collection(name):
        if name in ("jobs-dev", "youtube_upload_queue"):
            extra = getattr(db, "extra", {})
            return SimpleNamespace(document=lambda i: SimpleNamespace(
                get=lambda: SimpleNamespace(exists=extra.get((name, i), False))))
        return real_collection(name)

    db.collection = collection
    storage = FakeStorage(files, updated)
    return StorageRetentionService(db=db, storage=storage, settings=_settings()), db, storage


ORPHAN_FILES = {
    "jobs/live/finals/lossy_4k_mp4.mp4": 10,
    "jobs/ghost/input/song.flac": 100,
    "jobs/ghost/finals/lossy_720p_mp4.mp4": 30,
    "jobs/ghost/videos/with_vocals.mkv": 200,
    "jobs/ghost/stems/custom_instrumental.flac": 40,
    "jobs/ghost/custom_instrumental.mp3": 9,
    "jobs/fresh/finals/x.mp4": 50,
    "jobs/queued/finals/x.mp4": 70,
    "jobs/_preview123/review-audio/mixed.ogg": 4,
    "storage-retention/deletion-logs/old.jsonl": 1,
    "uploads/ghost/audio/x.flac": 5,
}


class TestOrphans:
    def _svc(self):
        updated = {"jobs/fresh/finals/x.mp4": NOW - timedelta(days=2)}
        return _orphan_service(ORPHAN_FILES, updated, records={"youtube_upload_queue": ["queued"]})

    def test_plan_keeps_only_input(self):
        objs = [{"name": k, "size": v, "updated": NOW - timedelta(days=90)}
                for k, v in ORPHAN_FILES.items() if k.startswith("jobs/ghost/")]
        plan = sr.plan_orphan_folder("ghost", objs, NOW)
        assert [m["name"] for m in plan["keep"]] == ["jobs/ghost/input/song.flac"]
        assert plan["purge_bytes"] == 30 + 200 + 40 + 9 and plan["skip_reason"] is None

    def test_recent_folder_is_skipped(self):
        plan = sr.plan_orphan_folder("x", [{"name": "jobs/x/a.mp4", "size": 1, "updated": NOW - timedelta(days=29)}], NOW)
        assert plan["skip_reason"] == "recently_modified"

    def test_dry_run_reports_without_deleting(self):
        svc, db, storage = self._svc()
        report = svc.purge_orphans(dry_run=True, now=NOW)
        assert storage.deleted == [] and storage.texts == {}
        assert report["candidates"] == 4  # ghost, fresh, queued, _preview123 (never "live")
        assert sorted(f["job_id"] for f in report["folders"]) == ["_preview123", "ghost"]
        assert report["skipped"] == {"recently_modified": 1, "has_record": 1}
        assert report["not_orphans"] == [{"job_id": "queued", "collection": "youtube_upload_queue"}]
        assert report["summary"]["gib_input_kept"] == round(100 / 2**30, 2)
        assert report["report_path"].endswith("-orphans-dry-run.json")

    def test_real_run_deletes_non_input_and_logs(self):
        svc, db, storage = self._svc()
        report = svc.purge_orphans(dry_run=False, now=NOW)
        assert sorted(storage.deleted) == sorted([
            "jobs/ghost/finals/lossy_720p_mp4.mp4", "jobs/ghost/videos/with_vocals.mkv",
            "jobs/ghost/stems/custom_instrumental.flac", "jobs/ghost/custom_instrumental.mp3",
            "jobs/_preview123/review-audio/mixed.ogg",
        ])
        for kept in ("jobs/ghost/input/song.flac", "jobs/live/finals/lossy_4k_mp4.mp4",
                     "jobs/fresh/finals/x.mp4", "jobs/queued/finals/x.mp4",
                     "storage-retention/deletion-logs/old.jsonl", "uploads/ghost/audio/x.flac"):
            assert kept in storage.files
        lines = _log_lines(storage, report["deletion_log"])
        assert report["deletion_log"].endswith("-orphan.jsonl")
        assert {l["category"] for l in lines} == {"orphan-nonInput"} and {l["mode"] for l in lines} == {"orphan"}
        assert storage.reports[report["deletion_summary"]]["objects_deleted"] == 5

    def test_record_created_after_listing_is_respected(self):
        svc, db, storage = self._svc()
        db.extra = {("jobs-dev", "ghost"): True}
        svc.purge_orphans(dry_run=False, now=NOW)
        assert not any(p.startswith("jobs/ghost/") for p in storage.deleted)

    def test_batch_limit(self):
        svc, db, storage = self._svc()
        report = svc.purge_orphans(dry_run=False, max_folders=1, now=NOW)
        assert len(report["folders"]) == 1 and report["skipped"]["batch_limit"] == 3
