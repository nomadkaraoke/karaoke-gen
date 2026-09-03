#!/usr/bin/env python3
"""
Bulk analyze + rewrite YouTube video descriptions for the Nomad Karaoke channel.

This is the manual/one-off driver. The classification, targeting, rendering and
update-body logic live in `backend.services.youtube_backfill` and are SHARED with
the scheduled daily drain worker, so results are identical either way. Rendering
itself uses `backend.services.youtube_description` (single source of truth for the
template, also used by the live upload pipeline).

Three phases:

  analyze   Read-only. Enumerate every video on the channel, categorize it,
            parse artist/title/brand-code, and write a report + full old→new
            diffs for review. Writes nothing to YouTube.

  apply     Write. Rewrite descriptions for every target (resumable, throttled,
            daily-quota-capped). Preserves title/category/language; description
            replaced; tags refreshed only with --update-tags.

  status    Print progress from saved state.

Auth reuses the production `youtube-oauth-credentials` Secret Manager secret. If
ADC can't read it, pass `--credentials-file` with a local JSON export.

QUOTA (YouTube Data API v3, default 10,000 units/day, SHARED with uploads):
  playlistItems.list = 1/page · videos.list = 1/50 · videos.update = 50 each

Examples:
  python youtube_descriptions.py analyze
  python youtube_descriptions.py apply --video-id VIDEOID --update-tags
  python youtube_descriptions.py apply --dry-run
  python youtube_descriptions.py apply --daily-quota 6000 --update-tags
  python youtube_descriptions.py status
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# --- Make the karaoke-gen repo importable (repo root is two levels up) ---
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.services.youtube_backfill import (  # noqa: E402
    UPDATE_COST,
    build_entries,
    build_update_snippet,
    build_youtube,
    fetch_snippets,
    get_uploads_playlist,
    iter_all_video_ids,
    load_credentials_from_secret,
    load_skip_ids,
    render_for,
)

STATE_DIR = _SCRIPT_DIR / ".state"
ANALYSIS_PATH = STATE_DIR / "analysis.json"
PROGRESS_PATH = STATE_DIR / "progress.json"
REPORT_PATH = STATE_DIR / "report.md"
DIFF_PATH = STATE_DIR / "review-diffs.txt"

# ----------------------------------------------------------------------------
# Auth / client (thin CLI wrapper around the shared helpers)
# ----------------------------------------------------------------------------
def load_credentials(credentials_file: Optional[str]) -> Dict:
    if credentials_file:
        with open(credentials_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    import os

    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
    creds = load_credentials_from_secret()
    if not creds:
        raise SystemExit(
            "Could not load YouTube credentials from Secret Manager.\n"
            "Either run `gcloud auth login` (an account that can read the "
            "`youtube-oauth-credentials` secret) and export it "
            "(`gcloud secrets versions access latest --secret=youtube-oauth-credentials "
            "> creds.json`), then pass --credentials-file creds.json."
        )
    return creds


# ----------------------------------------------------------------------------
# State helpers
# ----------------------------------------------------------------------------
def _load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return default


def _save_json(path: Path, data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------
# analyze
# ----------------------------------------------------------------------------
def cmd_analyze(args):
    youtube = build_youtube(load_credentials(args.credentials_file))

    print("Resolving uploads playlist...")
    playlist_id = get_uploads_playlist(youtube)
    print(f"Uploads playlist: {playlist_id}")

    print("Listing all video IDs (this is cheap)...")
    ids = iter_all_video_ids(youtube, playlist_id)
    if args.limit:
        ids = ids[: args.limit]
    print(f"Found {len(ids)} videos. Fetching snippets...")

    videos = fetch_snippets(youtube, ids)
    entries = build_entries(videos, ids)

    # Read-only quota estimate: channels.list(1) + playlistItems pages + videos.list batches.
    quota_estimate = 1 + (len(ids) + 49) // 50 + (len(ids) + 49) // 50

    analysis = {
        "generated_at": _now_iso(),
        "channel_video_count": len(ids),
        "quota_spent_estimate": quota_estimate,
        "entries": entries,
    }
    _save_json(ANALYSIS_PATH, analysis)
    _write_report(entries, quota_estimate)
    _write_diffs(entries, limit=args.diff_limit)

    print(f"\nQuota spent (read-only): ~{quota_estimate} units")
    print(f"Analysis : {ANALYSIS_PATH}")
    print(f"Report   : {REPORT_PATH}")
    print(f"Diffs    : {DIFF_PATH}")
    print("\nReview the report + diffs, adjust the skip/include lists in "
          "backend/data/youtube_backfill/, then run `apply`.")


def _counts(entries: List[Dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in entries:
        out[e[key]] = out.get(e[key], 0) + 1
    return out


def _write_report(entries: List[Dict], quota_estimate: int):
    targets = [e for e in entries if e["will_change"]]
    forced = [e for e in entries if e.get("forced_include") and e["will_change"]]
    non_karaoke = [e for e in entries if not e["is_karaoke"]]
    skipped = [e for e in entries if e["in_skip_list"]]
    review_candidates = [
        e for e in entries
        if e["is_karaoke"] and not e["target"] and not e["in_skip_list"]
        and e["parse_confidence"] == "medium"
    ]
    unparsed = [
        e for e in entries
        if e["is_karaoke"] and not e["target"] and not e["in_skip_list"]
        and e["parse_confidence"] == "none"
    ]

    lines = []
    lines.append("# YouTube Description Bulk-Rewrite — Analysis Report\n")
    lines.append(f"_Generated {_now_iso()} · read-only quota ~{quota_estimate} units_\n")
    lines.append(f"- **Total videos:** {len(entries)}")
    lines.append(f"- **Will be rewritten (target + changed):** {len(targets)}")
    lines.append(f"  → est. apply quota: **{len(targets) * UPDATE_COST:,} units** "
                 f"({len(targets)} × {UPDATE_COST})")
    lines.append(f"  (of which force-included: {len(forced)})")
    lines.append(f"- **Non-karaoke (never touched):** {len(non_karaoke)}")
    lines.append(f"- **Karaoke, parsed but not high-confidence (review):** {len(review_candidates)}")
    lines.append(f"- **Karaoke but unparseable title (needs review):** {len(unparsed)}")
    lines.append(f"- **In skip list:** {len(skipped)}\n")

    lines.append("## Breakdown by template kind")
    for k, n in sorted(_counts(entries, "kind").items(), key=lambda x: -x[1]):
        lines.append(f"- `{k}`: {n}")
    lines.append("")

    lines.append("## 🟡 Review candidates — karaoke, parsed, but not high-confidence")
    lines.append("Add genuine tracks to include_ids.txt; add the rest to skip_ids.txt.\n")
    for e in review_candidates:
        lines.append(f"- `{e['video_id']}` — {e['yt_title']!r} "
                     f"(kind=`{e['kind']}`, brand={e['brand_code']}, "
                     f"would-parse artist={e['artist']!r} title={e['song_title']!r})")
    lines.append("")

    lines.append("## 🟡 Karaoke but unparseable title")
    for e in unparsed:
        lines.append(f"- `{e['video_id']}` — {e['yt_title']!r} (`{e['kind']}`)")
    lines.append("")

    if non_karaoke:
        lines.append("## Non-karaoke videos (never touched)")
        for e in non_karaoke:
            lines.append(f"- `{e['video_id']}` — {e['yt_title']!r} (`{e['kind']}`)")
        lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_diffs(entries: List[Dict], limit: Optional[int]):
    targets = [e for e in entries if e["will_change"]]
    if limit:
        targets = targets[:limit]
    blocks = []
    for e in targets:
        new_desc = render_for(e)
        blocks.append(
            f"{'=' * 80}\n"
            f"VIDEO {e['video_id']}  |  {e['yt_title']}\n"
            f"artist={e['artist']!r}  title={e['song_title']!r}  brand={e['brand_code']}\n"
            f"{'-' * 34} BEFORE {'-' * 34}\n"
            f"{e['current_description']}\n"
            f"{'-' * 35} AFTER {'-' * 35}\n"
            f"{new_desc}\n"
        )
    DIFF_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIFF_PATH.write_text("\n".join(blocks), encoding="utf-8")


# ----------------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------------
def cmd_apply(args):
    analysis = _load_json(ANALYSIS_PATH, None)
    if not analysis and not args.video_id:
        raise SystemExit(f"No analysis found at {ANALYSIS_PATH}. Run `analyze` first.")

    progress = _load_json(PROGRESS_PATH, {"done": [], "daily": {}, "errors": {}})
    done = set(progress["done"])
    skip_ids = load_skip_ids()

    entries = analysis["entries"] if analysis else []
    by_id = {e["video_id"]: e for e in entries}

    if args.video_id:
        target_ids = [args.video_id]
    else:
        target_ids = [
            e["video_id"] for e in entries
            if e["will_change"] and e["video_id"] not in skip_ids
        ]

    # An explicit --video-id is a force request: ignore the resume-state.
    todo = target_ids if args.video_id else [vid for vid in target_ids if vid not in done]
    print(f"Targets: {len(target_ids)} · already done: "
          f"{len(target_ids) - len(todo)} · remaining: {len(todo)}")

    today = _today()
    spent_today = progress["daily"].get(today, 0)
    print(f"Quota used by this tool today: {spent_today} / {args.daily_quota} unit cap")

    youtube = None if args.dry_run else build_youtube(load_credentials(args.credentials_file))

    updated = 0
    for vid in todo:
        if updated >= args.max_updates:
            print(f"Reached --max-updates ({args.max_updates}). Stopping.")
            break
        if spent_today + UPDATE_COST > args.daily_quota:
            print(f"Reached daily quota cap ({args.daily_quota}). Re-run later.")
            break

        entry = by_id.get(vid)
        if entry is None:
            from backend.services.youtube_backfill import classify

            live = fetch_snippets(youtube, [vid])
            if vid not in live:
                print(f"  {vid}: not found; skipping")
                continue
            entry = classify(live[vid])

        if args.dry_run:
            print(f"[dry-run] would update {vid} — {entry['yt_title']}")
            updated += 1
            continue

        try:
            current = _fetch_full_snippet(youtube, vid)
            body = build_update_snippet(current, entry, enrich_tags=args.update_tags)
            youtube.videos().update(part="snippet", body={"id": vid, "snippet": body}).execute()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            progress["errors"][vid] = msg
            _save_json(PROGRESS_PATH, progress)
            if "quotaExceeded" in msg:
                print(f"  {vid}: quota exceeded (API). Stopping; re-run later.")
                break
            print(f"  {vid}: ERROR {msg}")
            continue

        done.add(vid)
        progress["done"] = sorted(done)
        spent_today += UPDATE_COST
        progress["daily"][today] = spent_today
        progress["errors"].pop(vid, None)
        _save_json(PROGRESS_PATH, progress)
        updated += 1
        print(f"  ✓ {vid} — {entry['yt_title']}")
        time.sleep(args.sleep)

    print(f"\nUpdated this run: {updated}")
    print(f"Total done: {len(done)} / {len(target_ids)} targets")
    if not args.dry_run:
        print(f"Tool quota used today: {progress['daily'].get(today, 0)}")


def _fetch_full_snippet(youtube, video_id: str) -> Dict:
    resp = youtube.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("video not found")
    return items[0]["snippet"]


# ----------------------------------------------------------------------------
# status
# ----------------------------------------------------------------------------
def cmd_status(args):
    analysis = _load_json(ANALYSIS_PATH, None)
    progress = _load_json(PROGRESS_PATH, {"done": [], "daily": {}, "errors": {}})
    if analysis:
        targets = [e for e in analysis["entries"] if e["will_change"]]
        print(f"Analysis: {analysis['generated_at']} · {analysis['channel_video_count']} videos")
        print(f"Targets for rewrite: {len(targets)}")
    else:
        print("No analysis yet. Run `analyze`.")
    print(f"Done: {len(progress['done'])}")
    print(f"Errors: {len(progress['errors'])}")
    if progress["daily"]:
        print("Quota used by day:")
        for d, n in sorted(progress["daily"].items()):
            print(f"  {d}: {n}")


# ----------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--credentials-file", help="Local JSON of youtube-oauth-credentials (else Secret Manager)")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="Read-only: enumerate + categorize + write report/diffs")
    a.add_argument("--limit", type=int, help="Only process first N videos (testing)")
    a.add_argument("--diff-limit", type=int, default=1700, help="Max diffs to write to review file")
    a.set_defaults(func=cmd_analyze)

    ap = sub.add_parser("apply", help="Write: rewrite descriptions (resumable, quota-capped)")
    ap.add_argument("--video-id", help="Update a single video (great for a first live test)")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, write nothing")
    ap.add_argument("--daily-quota", type=int, default=6000,
                    help="Max quota units this tool may spend per day (default 6000; upload pool is 10000)")
    ap.add_argument("--max-updates", type=int, default=10000, help="Max updates this run")
    ap.add_argument("--update-tags", action="store_true", help="Also refresh tags (default: preserve existing)")
    ap.add_argument("--sleep", type=float, default=0.2, help="Seconds between updates")
    ap.set_defaults(func=cmd_apply)

    s = sub.add_parser("status", help="Print progress")
    s.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
