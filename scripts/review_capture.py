#!/usr/bin/env python3
"""Capture a completed job's review into the automation corpus.

Given a job that Andrew has already reviewed (or an auto/unedited job), this:
  1. reads ``corrections.json`` + ``corrections_updated.json`` from GCS and the job
     doc from Firestore (READ-ONLY — safe under the claude-readonly ADC),
  2. reconstructs exactly what was changed in the lyrics (``lyrics_diff``),
  3. runs the shadow auto-approvability scorer,
  4. writes a per-job Markdown record (with blanks for Andrew's reasoning) + a
     machine-readable JSONL row into the corpus.

The *reasoning* — "what did I correct and why" / "what did I hear and how did I
decide on backing vocals" — is filled in during the interactive Claude session
(Andrew dictates / shares screenshots; the agent edits the Markdown). This script
just assembles everything factual so that conversation has the diff + signals in
front of it.

Usage:
    python scripts/review_capture.py <job_id>
    python scripts/review_capture.py <job_id> --print            # don't write, just show
    python scripts/review_capture.py <job_id> --output-dir /path/to/corpus

Corpus defaults to  $AUTOMATION_CORPUS_DIR  or the workspace-root
``docs/automation-corpus`` (private; keeps customer data out of the public repo).

See docs/archive/2026-08-25-full-auto-review-design.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Make ``backend`` importable when run from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.services.auto_approval.lyrics_diff import compute_lyrics_diff  # noqa: E402
from backend.services.auto_approval.scorer import score_job  # noqa: E402

# Prod bucket (config's "karaoke-gen-storage" default is overridden by env in prod).
GCS_BUCKET = os.getenv("GCS_BUCKET_NAME", "karaoke-gen-storage-nomadkaraoke")
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
DEFAULT_CORPUS_DIR = (
    os.getenv("AUTOMATION_CORPUS_DIR")
    or str(_REPO_ROOT.parent / "docs" / "automation-corpus")
)


def _read_gcs_json(path: str) -> Optional[Dict[str, Any]]:
    from google.cloud import storage  # lazy import

    client = storage.Client(project=GCP_PROJECT)
    blob = client.bucket(GCS_BUCKET).blob(path)
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


def _get_job_doc(job_id: str) -> Optional[Dict[str, Any]]:
    from google.cloud import firestore  # lazy import

    db = firestore.Client(project=GCP_PROJECT)
    doc = db.collection("jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None


def gather(job_id: str) -> Dict[str, Any]:
    """Collect everything factual about a job's review. Read-only."""
    job = _get_job_doc(job_id)
    if job is None:
        raise SystemExit(f"Job {job_id} not found in Firestore (project {GCP_PROJECT}).")

    state = job.get("state_data") or {}
    corrections = _read_gcs_json(f"jobs/{job_id}/lyrics/corrections.json")
    if corrections is None:
        raise SystemExit(
            f"No corrections.json for job {job_id} — not a lyrics job or not yet transcribed."
        )
    corrections_updated = _read_gcs_json(f"jobs/{job_id}/lyrics/corrections_updated.json")
    backing = state.get("backing_vocals_analysis")

    diff = compute_lyrics_diff(corrections, corrections_updated)
    verdict = score_job(corrections, backing)

    return {
        "job_id": job_id,
        "artist": job.get("artist") or job.get("display_artist") or "",
        "title": job.get("title") or job.get("display_title") or "",
        "status": job.get("status") or "",
        "is_tenant": bool(job.get("tenant_id")),
        "tenant_id": job.get("tenant_id") or "",
        "made_for_you": bool(job.get("made_for_you")),
        "instrumental_selection": state.get("instrumental_selection"),
        "was_edited": corrections_updated is not None and diff.has_changes,
        "diff": diff,
        "verdict": verdict,
        "backing_present": backing is not None,
    }


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_markdown(g: Dict[str, Any], captured_at: str) -> str:
    diff = g["diff"]
    v = g["verdict"]
    ls, bs = v.lyrics.signals, v.backing.signals

    lines: list[str] = []
    A = lines.append
    title = f"{g['artist']} — {g['title']}".strip(" —") or g["job_id"]
    A(f"# {title}")
    A("")
    A(f"- **Job:** `{g['job_id']}`")
    A(f"- **Captured:** {captured_at}")
    A(f"- **Status:** {g['status']}")
    src = "tenant:" + g["tenant_id"] if g["is_tenant"] else ("made-for-you" if g["made_for_you"] else "public")
    A(f"- **Source:** {src}")
    A(f"- **Instrumental chosen:** `{g['instrumental_selection']}`")
    A("")

    # --- Shadow verdict ---
    A("## Shadow auto-approvability verdict")
    A("")
    A(f"- **Overall would-auto:** {'✅ YES' if v.overall_auto else '❌ no'} "
      f"(scorer v{v.scorer_version})")
    A(f"- **Lyrics:** `{v.lyrics.verdict.value}` / tier `{v.lyrics.tier}`")
    for r in v.lyrics.reasons:
        A(f"  - {r}")
    A(f"    - anchor coverage {_fmt_pct(ls.anchor_word_fraction)} "
      f"({ls.anchor_word_count}/{ls.total_words} words), "
      f"unresolved-gap {_fmt_pct(ls.uncorrected_gap_fraction)} "
      f"({ls.gap_sequences_count} gaps)")
    A(f"    - reference sources: {ls.accepted_reference_sources or 'none'} "
      f"(synced: {ls.has_synced_reference})")
    A(f"- **Backing:** `{v.backing.verdict.value}` "
      f"(non-subjective: {v.backing.non_subjective})")
    for r in v.backing.reasons:
        A(f"  - {r}")
    if bs.analysis_present:
        A(f"    - audible {bs.audible_percentage:.1f}%, {bs.segment_count} segments, "
          f"{bs.loud_segment_count} loud, energy-rec `{bs.recommended_selection}`")
    A("")

    # --- What actually changed ---
    A("## What was changed in review (raw transcription → final)")
    A("")
    if not diff.has_changes:
        A("**No lyric edits** — the reviewer accepted the transcription as-is. "
          "(Strong signal this class may be auto-approvable.)")
    else:
        A(f"**{diff.total_changes} changes**: "
          f"{len(diff.replacements)} word swaps, {len(diff.text_edits)} in-place text, "
          f"{len(diff.timing_changes)} timing, "
          f"{len(diff.deletions)} deleted, {len(diff.insertions)} inserted, "
          f"{len(diff.segment_moves)} moved; "
          f"segmentation changed: {diff.segmentation_changed} "
          f"({diff.original_segment_count}→{diff.final_segment_count} lines).")
        A("")
        if diff.replacements:
            A("**Word corrections (mis-transcriptions fixed):**")
            for r in diff.replacements:
                at = f", @{r.start_time:.1f}s" if r.start_time is not None else ""
                A(f"- `{r.original_text}` → `{r.final_text}` (line {r.segment_index}{at})")
        if diff.text_edits:
            A("")
            A("**In-place text edits:**")
            for e in diff.text_edits:
                A(f"- `{e.original_text}` → `{e.final_text}` (line {e.segment_index})")
        if diff.timing_changes:
            A("")
            A("**Timing nudges:**")
            for t in diff.timing_changes:
                A(f"- `{t.text}`: start {t.start_delta:+}s, end {t.end_delta:+}s"
                  if t.start_delta is not None and t.end_delta is not None
                  else f"- `{t.text}`: timing changed")
        if diff.deletions:
            A("")
            A("**Deleted:** " + ", ".join(f"`{d.text}`" for d in diff.deletions))
        if diff.insertions:
            A("")
            A("**Inserted:** " + ", ".join(f"`{i.text}`" for i in diff.insertions))
        if diff.segment_moves:
            A("")
            A("**Moved between lines:** "
              + ", ".join(f"`{m.text}` ({m.original_segment_index}→{m.final_segment_index})"
                          for m in diff.segment_moves))
    A("")

    # --- Reasoning (filled in during the interactive session) ---
    A("## Lyrics — what I corrected and WHY  ⟵ *(fill in)*")
    A("")
    A("> For each edit above, why did the AI get it wrong and how did you know the "
      "right answer? Was it audible-but-mistranscribed, an adlib, a formatting/line-break "
      "call, a timing feel thing, a homophone, a proper noun, etc.? Note anything a checker "
      "could have caught automatically.")
    A("")
    A("_(dictate here)_")
    A("")
    A("## Backing vocals — what I heard and how I decided  ⟵ *(fill in)*")
    A("")
    A("> What did the backing-vocals preview sound like? Was it clean harmonies worth "
      "keeping, lead-vocal bleed, adlibs, adds nothing? Why `"
      f"{g['instrumental_selection']}`? What would have made this a clear yes/no vs. a "
      "genuine judgement call?")
    A("")
    A("_(dictate here)_")
    A("")
    A("## Screenshots")
    A("")
    A("_(paste / reference any screenshots from the review here)_")
    A("")
    return "\n".join(lines)


def _jsonl_row(g: Dict[str, Any], captured_at: str) -> Dict[str, Any]:
    v = g["verdict"]
    diff = g["diff"]
    return {
        "job_id": g["job_id"],
        "captured_at": captured_at,
        "artist": g["artist"],
        "title": g["title"],
        "source": ("tenant" if g["is_tenant"] else ("mfy" if g["made_for_you"] else "public")),
        "tenant_id": g["tenant_id"],
        "instrumental_selection": g["instrumental_selection"],
        "overall_would_auto": v.overall_auto,
        "lyrics_verdict": v.lyrics.verdict.value,
        "lyrics_tier": v.lyrics.tier,
        "backing_verdict": v.backing.verdict.value,
        "backing_non_subjective": v.backing.non_subjective,
        "lyrics_signals": asdict(v.lyrics.signals),
        "backing_signals": asdict(v.backing.signals),
        "diff_summary": {
            "has_changes": diff.has_changes,
            "total_changes": diff.total_changes,
            "replacements": len(diff.replacements),
            "text_edits": len(diff.text_edits),
            "timing_changes": len(diff.timing_changes),
            "deletions": len(diff.deletions),
            "insertions": len(diff.insertions),
            "segment_moves": len(diff.segment_moves),
            "segmentation_changed": diff.segmentation_changed,
        },
        # reasoning is added by hand in the .md; this flag tracks corpus completeness
        "reasoning_captured": False,
    }


def write_corpus(g: Dict[str, Any], out_dir: str, captured_at: str) -> str:
    corpus = Path(out_dir)
    (corpus / "jobs").mkdir(parents=True, exist_ok=True)
    md_path = corpus / "jobs" / f"{g['job_id']}.md"
    if md_path.exists():
        print(f"⚠️  {md_path} already exists — not overwriting (edit it directly).")
    else:
        md_path.write_text(render_markdown(g, captured_at), encoding="utf-8")
        print(f"📝 Wrote {md_path}")

    # Append/replace JSONL row (idempotent on job_id).
    jsonl_path = corpus / "corpus.jsonl"
    rows: list[Dict[str, Any]] = []
    if jsonl_path.exists():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("job_id") != g["job_id"]:
                    rows.append(r)
    rows.append(_jsonl_row(g, captured_at))
    jsonl_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    print(f"📊 Updated {jsonl_path} ({len(rows)} jobs in corpus)")
    return str(md_path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--output-dir", default=DEFAULT_CORPUS_DIR)
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="print the record to stdout without writing to the corpus")
    args = ap.parse_args()

    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    g = gather(args.job_id)

    md = render_markdown(g, captured_at)
    if args.print_only:
        print(md)
        return

    print(md)
    print("\n" + "=" * 70)
    write_corpus(g, args.output_dir, captured_at)
    print("\nNext: open the .md in the Claude session and dictate the two reasoning "
          "sections (lyrics WHY + backing-vocals decision).")


if __name__ == "__main__":
    main()
