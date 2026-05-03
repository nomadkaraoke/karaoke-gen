"""One-shot script to populate the year-5-stars fixture from a real production job.

Run once:
    poetry run python -m backend.eval.custom_lyrics._bootstrap_year5
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from google.cloud import firestore


JOB_ID = "2cb49a45"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "year-5-stars-shake-it-off"
CLIENT_INPUT_SRC = Path("/Users/andrew/Projects/nomadkaraoke/year-5-stars-client-custom-lyrics.txt")


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    fs = firestore.Client(project="nomadkaraoke")
    job_doc = fs.collection("jobs").document(JOB_ID).get()
    if not job_doc.exists:
        raise SystemExit(f"Job {JOB_ID} not found")
    job = job_doc.to_dict()

    # Corrected lyrics are stored inline in state_data (not in GCS for this job)
    corrected = (
        job.get("state_data", {}).get("corrected_lyrics")
        or job.get("corrected_lyrics")
        or {}
    )
    if not corrected:
        # Fallback: try GCS path if present
        corrected_path = job.get("corrected_json_gcs_path") or job.get("lyrics", {}).get("corrected_path")
        if corrected_path:
            from google.cloud import storage  # noqa: PLC0415
            storage_client = storage.Client(project="nomadkaraoke")
            bucket_name, *blob_parts = corrected_path.replace("gs://", "").split("/", 1)
            blob = storage_client.bucket(bucket_name).blob(blob_parts[0])
            corrected = json.loads(blob.download_as_bytes())
        else:
            raise SystemExit(f"Job {JOB_ID} has no corrected lyrics (checked state_data and GCS path fields)")

    segments = corrected.get("corrected_segments") or corrected.get("segments") or []
    if not segments:
        raise SystemExit("No segments found in corrected.json")

    (FIXTURE_DIR / "original_segments.json").write_text(json.dumps(segments, indent=2))
    (FIXTURE_DIR / "original_lyrics.txt").write_text(
        "\n".join(seg.get("text", "").strip() for seg in segments) + "\n"
    )

    if CLIENT_INPUT_SRC.exists():
        shutil.copy(CLIENT_INPUT_SRC, FIXTURE_DIR / "client_input.txt")
    else:
        print(f"WARNING: {CLIENT_INPUT_SRC} not found; populate client_input.txt manually")

    metadata = {
        "id": "year-5-stars-shake-it-off",
        "artist": job.get("artist") or "Taylor Swift",
        "title": job.get("title") or "Shake It Off",
        "source_job_id": JOB_ID,
        "difficulty": "hard",
        "input_style": "long substantive lines, name-heavy, mismatched syllable budget",
        "settings_to_test": [
            {"name": "default", "allow_reword": True, "allow_omit": True, "fixed_line_count": True, "strictness": "balanced"},
            {"name": "verbatim", "allow_reword": True, "allow_omit": True, "fixed_line_count": True, "strictness": "verbatim"},
            {"name": "strict", "allow_reword": True, "allow_omit": True, "fixed_line_count": True, "strictness": "strict"},
        ],
    }
    (FIXTURE_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2))

    print(f"Fixture populated at {FIXTURE_DIR}")
    print(f"  - {len(segments)} segments")


if __name__ == "__main__":
    main()
