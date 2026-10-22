"""Tests for the voice-pool separate-only worker (synthetic data only)."""
import json
import os

from backend.workers import voice_pool_separate_worker as w

BUCKET = "karaoke-gen-storage-nomadkaraoke"
MANIFEST = "gs://karaoke-gen-storage-nomadkaraoke/voice-pool/manifests/run1.jsonl"


class FakeStorage:
    def __init__(self, ids, existing=()):
        lines = "".join(
            json.dumps({"id": i, "audio": f"gs://{BUCKET}/voice-pool/audio/{i}/original.flac"}) + "\n" for i in ids
        )
        self.blobs = {"voice-pool/manifests/run1.jsonl": lines.encode()}
        for i in existing:
            self.blobs[f"voice-pool/stems/{i}/vocals.flac"] = b"x"
        self.downloads = []

    def download_bytes(self, p):
        return self.blobs[p]

    def file_exists(self, p):
        return p in self.blobs

    def download_file(self, src, dest):
        self.downloads.append(src)
        open(dest, "wb").write(b"audio")
        return dest

    def upload_file(self, local, dest):
        self.blobs[dest] = open(local, "rb").read()
        return dest

    def upload_text(self, dest, text, content_type="text/plain"):
        self.blobs[dest] = text.encode()
        return dest


class FakeSeparator:
    def __init__(self, out_dir, fail_ids=()):
        self.out_dir, self.fail_ids, self.calls = out_dir, fail_ids, 0

    def separate(self, path):
        self.calls += 1
        if any(f in str(self.calls) for f in self.fail_ids):
            raise RuntimeError("boom")
        paths = []
        for tag in ("Vocals", "Instrumental"):
            p = os.path.join(self.out_dir, f"{self.calls}_({tag})_preset_instrumental_clean.flac")
            open(p, "wb").write(tag.encode())
            paths.append(p)
        return paths


def _results(storage, idx=0):
    raw = storage.blobs[f"voice-pool/manifests/run1.results/part-{idx}.jsonl"].decode()
    return [json.loads(x) for x in raw.splitlines()]


def test_shard_complete_and_disjoint():
    lines = [{"id": str(i)} for i in range(23)]
    parts = [w.shard(lines, i, 4) for i in range(4)]
    flat = [x["id"] for p in parts for x in p]
    assert sorted(flat) == sorted(x["id"] for x in lines)
    assert len(set(flat)) == 23
    assert [x["id"] for x in parts[1]] == ["1", "5", "9", "13", "17", "21"]


def test_vocals_path_from_outputs():
    paths = [
        "/o/x_(Instrumental)_preset_instrumental_clean.flac",
        "/o/x_(Vocals)_preset_instrumental_clean.flac",
    ]
    assert w.vocals_path_from_outputs(paths) == paths[1]
    assert w.vocals_path_from_outputs(paths[:1]) is None
    assert w.vocals_path_from_outputs(["/o/x_(No Vocal)_preset_instrumental_clean.flac"]) is None


def test_run_manifest_skips_existing_and_records_failures(tmp_path):
    storage = FakeStorage(["a", "b", "c"], existing=["a"])
    sep = FakeSeparator(str(tmp_path), fail_ids=("1",))  # first real separation raises
    built = []

    def factory():
        built.append(1)
        return sep

    results = w.run_manifest(storage, factory, MANIFEST, 0, 1, bucket=BUCKET)
    by_id = {r["id"]: r for r in results}
    assert by_id["a"]["ok"] and by_id["a"]["seconds"] == 0
    assert not by_id["b"]["ok"] and "boom" in by_id["b"]["error"]
    assert by_id["c"]["ok"] and by_id["c"]["vocals"] == "voice-pool/stems/c/vocals.flac"
    assert storage.blobs["voice-pool/stems/c/vocals.flac"] == b"Vocals"
    assert "voice-pool/stems/b/vocals.flac" not in storage.blobs
    assert "voice-pool/audio/a/original.flac" not in storage.downloads
    assert len(built) == 1  # one separator reused
    assert _results(storage) == results
    assert os.listdir(tmp_path) == []  # stems cleaned up


def test_results_written_every_10(tmp_path, monkeypatch):
    ids = [str(i) for i in range(12)]
    storage = FakeStorage(ids)
    writes = []
    orig = w._write_results
    monkeypatch.setattr(w, "_write_results", lambda s, m, i, r: (writes.append(len(r)), orig(s, m, i, r)))
    w.run_manifest(storage, lambda: FakeSeparator(str(tmp_path)), MANIFEST, 0, 1, bucket=BUCKET)
    assert writes == [10, 12]


def test_sharded_task_only_processes_its_share(tmp_path):
    storage = FakeStorage([str(i) for i in range(6)])
    w.run_manifest(storage, lambda: FakeSeparator(str(tmp_path)), MANIFEST, 1, 3, bucket=BUCKET)
    assert [r["id"] for r in _results(storage, 1)] == ["1", "4"]


def test_separator_load_failure_fails_fast(tmp_path):
    import pytest

    storage = FakeStorage(["a", "b"])
    calls = []

    def factory():
        calls.append(1)
        raise RuntimeError("no gpu")

    with pytest.raises(w.SeparatorLoadError):
        w.run_manifest(storage, factory, MANIFEST, 0, 1, bucket=BUCKET)
    assert len(calls) == 1  # no per-song retry of the model load


def _patch_main(monkeypatch, run):
    import backend.services.storage_service as ss

    monkeypatch.setattr(ss, "StorageService", lambda: object())
    monkeypatch.setattr(w, "run_manifest", run)


def test_main_all_failed_logs_one_error_and_exits_nonzero(monkeypatch, caplog):
    fail = {"id": "a", "ok": False, "vocals": None, "seconds": 0, "error": "oom"}
    _patch_main(monkeypatch, lambda *a, **k: [fail, dict(fail, id="b")])
    assert w.main(["--manifest", MANIFEST]) == 1
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


def test_main_partial_failure_exits_zero_without_error(monkeypatch, caplog):
    ok = {"id": "a", "ok": True, "vocals": "v", "seconds": 1, "error": None}
    _patch_main(monkeypatch, lambda *a, **k: [ok, dict(ok, id="b", ok=False, error="x")])
    assert w.main(["--manifest", MANIFEST]) == 0
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_main_load_failure_exits_nonzero_with_one_error(monkeypatch, caplog):
    def boom(*a, **k):
        raise w.SeparatorLoadError("x")

    _patch_main(monkeypatch, boom)
    assert w.main(["--manifest", MANIFEST]) == 1
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


def test_periodic_flush_failure_is_tolerated_final_is_strict(tmp_path, monkeypatch):
    import pytest

    storage = FakeStorage([str(i) for i in range(11)])
    orig = w._write_results
    n = []

    def flaky(s, m, i, r):
        n.append(len(r))
        if len(n) == 1:
            raise OSError("transient gcs")
        return orig(s, m, i, r)

    monkeypatch.setattr(w, "_write_results", flaky)
    results = w.run_manifest(storage, lambda: FakeSeparator(str(tmp_path)), MANIFEST, 0, 1, bucket=BUCKET)
    assert len(results) == 11 and n == [10, 11]

    monkeypatch.setattr(w, "_write_results", lambda *a: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(OSError):
        w.run_manifest(FakeStorage(["a"]), lambda: FakeSeparator(str(tmp_path)), MANIFEST, 0, 1, bucket=BUCKET)


def test_wrong_bucket_song_recorded_and_manifest_fails(tmp_path):
    import pytest

    storage = FakeStorage(["a", "b"])
    storage.blobs["voice-pool/manifests/run1.jsonl"] = (
        json.dumps({"id": "a", "audio": "gs://other-bucket/voice-pool/audio/a/original.flac"}) + "\n"
        + json.dumps({"id": "b", "audio": f"gs://{BUCKET}/voice-pool/audio/b/original.flac"}) + "\n"
    ).encode()
    results = w.run_manifest(storage, lambda: FakeSeparator(str(tmp_path)), MANIFEST, 0, 1, bucket=BUCKET)
    assert results[0]["ok"] is False and results[0]["error"] == "wrong_bucket"
    assert results[1]["ok"] is True

    with pytest.raises(w.SystemicTaskError):
        w.run_manifest(storage, lambda: FakeSeparator(str(tmp_path)),
                       "gs://other-bucket/voice-pool/manifests/run1.jsonl", 0, 1, bucket=BUCKET)


def test_systemic_error_messages_are_task_independent(monkeypatch, caplog):
    fail = {"id": "a", "ok": False, "vocals": None, "seconds": 0, "error": "oom"}
    _patch_main(monkeypatch, lambda *a, **k: [fail])
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "3")
    w.main(["--manifest", MANIFEST])
    errs = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errs) == 1 and "3" not in errs[0].getMessage().split("(first")[0]
