#!/usr/bin/env python3
"""KaraokeHunt outreach — torrent eyeball list (from the phase1b cache, no API calls).

Andrew's actual acceptance rule (2026-09-13) is looser than the bulk-mode
pick_auto_selection gate phase1b used: **any FLAC torrent with >=2 seeders is
acceptable as long as it's the right song, verified by eyeballing the filename
in the torrent**. This script regroups every non-CONFIDENT song by that rule so
he can scan one table and name the few outliers:

  A. FLAC torrent, filename auto-matches the title  -> almost certainly fine
  B. FLAC torrent, filename does NOT strictly match -> eyeball the filename
  LOW-SEED flag on any A/B row whose best torrent has <2 seeders
  C. NO torrent at all (only Spotify/YouTube or zero results) -> needs Andrew's
     manual review/lookup before any job is submitted

Output: torrent_review.md next to the cache.

Usage:
  python scripts/karaokehunt_outreach/torrent_review.py \
      --out /path/to/outreach_out
"""
from __future__ import annotations

import argparse
import json
import os

TORRENT_PROVIDERS = {"red", "ops"}


def is_torrent(r: dict) -> bool:
    return (r or {}).get("is_lossless") is True and \
        ((r or {}).get("provider") or "").lower() in TORRENT_PROVIDERS


def fname(r: dict) -> str:
    tf = (r or {}).get("target_file") or ""
    return tf.split("/")[-1] if tf else ((r or {}).get("title") or "")


def fmt(r: dict) -> str:
    q = (r or {}).get("quality_data") or {}
    bits = q.get("bit_depth")
    return f"{q.get('format', '?')}{f' {bits}bit' if bits else ''}" \
           f"{f' · {q.get('media')}' if q.get('media') else ''}"


def esc(s: str) -> str:
    return (s or "").replace("|", "\\|")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="outreach_out dir (reads cache, writes md)")
    args = ap.parse_args()

    cache = json.load(open(os.path.join(args.out, "audio_search_cache.json")))

    confident, group_a, group_b, group_c = [], [], [], []
    for k, e in cache.items():
        if e.get("recheck", {}).get("available"):
            continue  # became community after typo-fix
        srch = e.get("search") or {}
        if not srch or srch.get("error"):
            continue
        c = e.get("canonical") or {"artist": e["artist"], "title": e["title"]}
        song = f"{c['artist']} – {c['title']}"
        best = srch.get("best") or {}
        row = {"key": k, "song": song, "best": best,
               "seeders": best.get("seeders") or 0,
               "results_count": srch.get("results_count", 0)}
        if srch.get("pick_index") is not None:
            confident.append(row)
        elif srch.get("near_miss"):
            group_a.append(row)
        elif is_torrent(best):
            group_b.append(row)
        else:
            # No usable torrent as best — note if a (vinyl) torrent lurks in top results
            row["vinyl_only"] = any(is_torrent(r) or
                                    ((r.get("is_lossless") is True) and
                                     ((r.get("quality_data") or {}).get("media") or "").lower() == "vinyl")
                                    for r in srch.get("top_results", []))
            group_c.append(row)

    # Dedupe display rows — distinct typed variants can canonicalize to the same
    # song (submissions still fan out per requester from actions_v2.json).
    def dedupe(rows, keyfn):
        seen, out = set(), []
        for r in rows:
            k = keyfn(r)
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out

    group_a = dedupe(group_a, lambda r: (r["song"].lower(), fname(r["best"])))
    group_b = dedupe(group_b, lambda r: (r["song"].lower(), fname(r["best"])))
    group_c = dedupe(group_c, lambda r: r["song"].lower())

    group_a.sort(key=lambda r: -r["seeders"])
    group_b.sort(key=lambda r: -r["seeders"])
    group_c.sort(key=lambda r: r["song"].lower())

    path = os.path.join(args.out, "torrent_review.md")
    with open(path, "w") as f:
        f.write("# Torrent eyeball list — best torrent per non-CONFIDENT song\n\n")
        f.write("Rule being applied (Andrew, 2026-09-13): any FLAC torrent with ≥2 seeders is "
                "acceptable if the torrent filename (roughly) matches the song. Scan A and B, "
                "call out row numbers to EXCLUDE; everything else gets submitted. "
                "C is the unavoidable manual-review list (no torrent exists).\n\n")
        f.write(f"- Already CONFIDENT (submitted list unchanged): **{len(confident)}**\n")
        f.write(f"- **A. torrent + filename auto-matches: {len(group_a)}** (expect ~all fine)\n")
        f.write(f"- **B. torrent but filename differs from typed title: {len(group_b)}** (eyeball)\n")
        f.write(f"- **C. no torrent at all: {len(group_c)}** (manual review before any job)\n\n")

        def table(rows, title, note):
            f.write(f"## {title}\n\n{note}\n\n")
            f.write("| # | Song (canonical) | Torrent filename | Seeders | Quality | Src |\n")
            f.write("|--:|---|---|--:|---|---|\n")
            for i, r in enumerate(rows, 1):
                b = r["best"]
                flag = " ⚠️LOW-SEED" if r["seeders"] < 2 else ""
                f.write(f"| {i} | {esc(r['song'])} | {esc(fname(b))}{flag} | "
                        f"{r['seeders']} | {esc(fmt(b))} | {b.get('provider', '')} |\n")
            f.write("\n")

        table(group_a, "A. Filename auto-matches (near-misses)",
              "Strict token check already passed — listed so you can catch wrong-version "
              "outliers (live/remix/etc.). ⚠️LOW-SEED = under your 2-seeder floor.")
        table(group_b, "B. Filename does NOT strictly match the typed title",
              "Usually typos in the original request (e.g. “Rapsody”) or extra words — "
              "compare the two columns; exclude rows where it's clearly a different song.")

        f.write("## C. No torrent found — manual review needed\n\n")
        f.write("| # | Song (canonical) | Results | Best non-torrent option |\n")
        f.write("|--:|---|--:|---|\n")
        for i, r in enumerate(group_c, 1):
            b = r["best"]
            extra = " *(vinyl-only torrent exists)*" if r.get("vinyl_only") else ""
            opt = f"[{b.get('provider', '—')}] {esc((b.get('title') or fname(b))[:70])}" if b else "—"
            f.write(f"| {i} | {esc(r['song'])} | {r['results_count']} | {opt}{extra} |\n")
        f.write("\n")

    print(f"WROTE {path}")
    print(f"confident={len(confident)} A={len(group_a)} B={len(group_b)} C={len(group_c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
