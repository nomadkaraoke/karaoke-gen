"""Tests for the voice-pool separate-only worker (synthetic data only)."""
import json
import os

from backend.workers import voice_pool_separate_worker as w

MANIFEST = "gs://karaoke-gen-storage-nomadkaraoke/voice-pool/manifests/run1.jsonl"


class FakeStorage:
    def __init__(self, ids, existing=()):
        lines = "".join(
            json.dumps({"id": i, "audio": f"gs://b/voice-pool/audio/{i}/original.flac"}) + "\n" for i in ids
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

    results = w.run_manifest(storage, factory, MANIFEST, 0, 1)
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
    w.run_manifest(storage, lambda: FakeSeparator(str(tmp_path)), MANIFEST, 0, 1)
    assert writes == [10, 12]


def test_sharded_task_only_processes_its_share(tmp_path):
    storage = FakeStorage([str(i) for i in range(6)])
    w.run_manifest(storage, lambda: FakeSeparator(str(tmp_path)), MANIFEST, 1, 3)
    assert [r["id"] for r in _results(storage, 1)] == ["1", "4"]
