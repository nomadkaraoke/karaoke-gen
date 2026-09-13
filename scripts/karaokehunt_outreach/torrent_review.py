#!/usr/bin/env python3
"""KaraokeHunt outreach — torrent eyeball list (from the phase1b cache, no API calls).

Andrew's actual acceptance rule (2026-09-13) is looser than the bulk-mode
pick_auto_selection gate phase1b used: **any FLAC torrent with >=2 seeders is
acceptable as long as it's the right song, verified by eyeballing the filename
in the torrent**. This script regroups every searched song by that rule so he
can scan one table and name the few outliers:

  CONFIDENT. passed the strict phase1b gate            -> submit unless vetoed
  A. FLAC torrent, filename auto-matches the title     -> almost certainly fine
  B. FLAC torrent, filename does NOT strictly match    -> eyeball the filename
  LOW-SEED flag on any A/B row whose best torrent has <2 seeders
  C. NO torrent at all (only Spotify/YouTube)          -> Andrew manual review

Each row carries the requester email(s) and the RAW unmodified submission text
from the KaraokeHunt app (artist/title/InputURL as typed).

Outputs (next to the cache):
  - torrent_review.md   — human-readable grouped tables
  - torrent_review.csv  — same data flat, one row per song, with an empty
    "Review notes" column; upload as a Google Sheet for Andrew to annotate.
    The trailing "key" column is the cache key used to map notes back.

Usage:
  python scripts/karaokehunt_outreach/torrent_review.py \
      --out /path/to/outreach_out \
      --requests /path/to/karaokehunt_track_requests.csv \
      --actions /path/to/outreach_out/actions.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re

TORRENT_PROVIDERS = {"red", "ops"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


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


def build_requester_map(actions_path: str, requests_path: str) -> dict:
    """cache key -> {'emails': [..], 'raw': [..verbatim submissions..]}"""
    actions = json.load(open(actions_path))
    out: dict[str, dict] = {}
    key_emails: dict[str, set] = {}
    for a in actions:
        for s in a.get("generate_songs", []):
            k = f"{norm(s['artist'])}||{norm(s['title'])}"
            key_emails.setdefault(k, set()).add(a["email"])
    if requests_path and os.path.exists(requests_path):
        for r in csv.DictReader(open(requests_path)):
            email = (r.get("email") or "").strip().lower()
            k = f"{norm(r.get('artist', ''))}||{norm(r.get('title', ''))}"
            if k not in key_emails or email not in key_emails[k]:
                continue
            raw = f"{r.get('artist', '')} / {r.get('title', '')}"
            url = (r.get("input_url") or "").strip()
            if url:
                raw += f" / URL: {url}"
            d = out.setdefault(k, {"emails": set(), "raw": []})
            d["emails"].add(email)
            if raw not in d["raw"]:
                d["raw"].append(raw)
    for k, emails in key_emails.items():
        d = out.setdefault(k, {"emails": set(), "raw": []})
        d["emails"] |= emails
    return {k: {"emails": sorted(d["emails"]), "raw": d["raw"]}
            for k, d in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="outreach_out dir (reads cache, writes md+csv)")
    ap.add_argument("--requests", default="", help="karaokehunt_track_requests.csv (raw submissions)")
    ap.add_argument("--actions", default="", help="phase1 actions.json (requester emails)")
    args = ap.parse_args()

    cache = json.load(open(os.path.join(args.out, "audio_search_cache.json")))
    requesters = build_requester_map(
        args.actions or os.path.join(args.out, "actions.json"),
        args.requests) if (args.actions or args.requests) else {}

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
        req = requesters.get(k, {"emails": [], "raw": []})
        row = {"key": k, "song": song, "best": best,
               "seeders": best.get("seeders") or 0,
               "results_count": srch.get("results_count", 0),
               "emails": req["emails"], "raw": req["raw"]}
        if srch.get("pick_index") is not None:
            row["best"] = srch.get("picked") or best
            row["seeders"] = row["best"].get("seeders") or 0
            confident.append(row)
        elif srch.get("near_miss"):
            group_a.append(row)
        elif is_torrent(best):
            group_b.append(row)
        else:
            row["vinyl_only"] = any(((r.get("is_lossless") is True) and
                                     ((r.get("quality_data") or {}).get("media") or "").lower() == "vinyl")
                                    for r in srch.get("top_results", []))
            group_c.append(row)

    # Dedupe display rows by song+filename, MERGING requester emails/raw text —
    # distinct typed variants can canonicalize to the same song (submissions
    # still fan out per requester from actions_v2.json).
    def dedupe(rows, keyfn):
        seen: dict = {}
        out = []
        for r in rows:
            k = keyfn(r)
            if k in seen:
                kept = seen[k]
                kept["emails"] = sorted(set(kept["emails"]) | set(r["emails"]))
                kept["raw"] = kept["raw"] + [x for x in r["raw"] if x not in kept["raw"]]
            else:
                seen[k] = r
                out.append(r)
        return out

    confident = dedupe(confident, lambda r: (r["song"].lower(), fname(r["best"])))
    group_a = dedupe(group_a, lambda r: (r["song"].lower(), fname(r["best"])))
    group_b = dedupe(group_b, lambda r: (r["song"].lower(), fname(r["best"])))
    group_c = dedupe(group_c, lambda r: r["song"].lower())

    confident.sort(key=lambda r: -r["seeders"])
    group_a.sort(key=lambda r: -r["seeders"])
    group_b.sort(key=lambda r: -r["seeders"])
    group_c.sort(key=lambda r: r["song"].lower())

    # ---------------- markdown ----------------
    md_path = os.path.join(args.out, "torrent_review.md")
    with open(md_path, "w") as f:
        f.write("# Torrent eyeball list — best torrent per searched song\n\n")
        f.write("Rule being applied (Andrew, 2026-09-13): any FLAC torrent with ≥2 seeders is "
                "acceptable if the torrent filename (roughly) matches the song. Scan CONFIDENT/A/B, "
                "note rows to EXCLUDE; everything else gets submitted. "
                "C is the unavoidable manual-review list (no torrent exists).\n\n")
        f.write(f"- **CONFIDENT (passed strict gate): {len(confident)}**\n")
        f.write(f"- **A. torrent + filename auto-matches: {len(group_a)}** (expect ~all fine)\n")
        f.write(f"- **B. torrent but filename differs from typed title: {len(group_b)}** (eyeball)\n")
        f.write(f"- **C. no torrent at all: {len(group_c)}** (manual review before any job)\n\n")

        def table(rows, title, note):
            f.write(f"## {title}\n\n{note}\n\n")
            f.write("| # | Song (canonical) | Torrent filename | Seeders | Quality | Src | Requested by | Raw submission |\n")
            f.write("|--:|---|---|--:|---|---|---|---|\n")
            for i, r in enumerate(rows, 1):
                b = r["best"]
                flag = " ⚠️LOW-SEED" if r["seeders"] < 2 else ""
                f.write(f"| {i} | {esc(r['song'])} | {esc(fname(b))}{flag} | "
                        f"{r['seeders']} | {esc(fmt(b))} | {b.get('provider', '')} | "
                        f"{esc(', '.join(r['emails']))} | {esc(' ‖ '.join(r['raw']))} |\n")
            f.write("\n")

        table(confident, "CONFIDENT — passed the strict phase1b gate",
              "Lossless torrent, ≥50 seeders, non-vinyl, filename verified. Veto anything odd.")
        table(group_a, "A. Filename auto-matches (near-misses)",
              "Strict token check already passed — listed so you can catch wrong-version "
              "outliers (live/remix/etc.). ⚠️LOW-SEED = under your 2-seeder floor.")
        table(group_b, "B. Filename does NOT strictly match the typed title",
              "Usually typos in the original request (e.g. “Rapsody”) or extra words — "
              "compare the two columns; exclude rows where it's clearly a different song.")

        f.write("## C. No torrent found — manual review needed\n\n")
        f.write("| # | Song (canonical) | Results | Best non-torrent option | Requested by | Raw submission |\n")
        f.write("|--:|---|--:|---|---|---|\n")
        for i, r in enumerate(group_c, 1):
            b = r["best"]
            extra = " *(vinyl-only torrent exists)*" if r.get("vinyl_only") else ""
            opt = f"[{b.get('provider', '—')}] {esc((b.get('title') or fname(b))[:70])}" if b else "—"
            f.write(f"| {i} | {esc(r['song'])} | {r['results_count']} | {opt}{extra} | "
                    f"{esc(', '.join(r['emails']))} | {esc(' ‖ '.join(r['raw']))} |\n")
        f.write("\n")

    # ---------------- csv (for Google Sheet) ----------------
    csv_path = os.path.join(args.out, "torrent_review.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Group", "#", "Song (canonical)", "Torrent filename", "Seeders",
                    "Quality", "Source", "Requested by", "Raw submission",
                    "Results", "Best non-torrent option", "Review notes", "key"])
        for group, rows in (("CONFIDENT", confident), ("A", group_a),
                            ("B", group_b), ("C", group_c)):
            for i, r in enumerate(rows, 1):
                b = r["best"]
                low = " LOW-SEED" if group in ("A", "B") and r["seeders"] < 2 else ""
                nontorrent = ""
                if group == "C":
                    nontorrent = (f"[{b.get('provider', '—')}] {(b.get('title') or fname(b))[:80]}"
                                  if b else "—")
                    if r.get("vinyl_only"):
                        nontorrent += " (vinyl-only torrent exists)"
                w.writerow([group + low, i, r["song"],
                            fname(b) if group != "C" else "",
                            r["seeders"] if group != "C" else "",
                            fmt(b) if group != "C" else "",
                            b.get("provider", "") if group != "C" else "",
                            ", ".join(r["emails"]), " ‖ ".join(r["raw"]),
                            r["results_count"], nontorrent, "", r["key"]])

    print(f"WROTE {md_path}\nWROTE {csv_path}")
    print(f"confident={len(confident)} A={len(group_a)} B={len(group_b)} C={len(group_c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
