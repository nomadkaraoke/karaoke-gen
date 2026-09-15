#!/usr/bin/env python3
"""KaraokeHunt outreach — Phase 1c (canonicalization v2 + relaxed verdicts, ZERO spend).

Applies the human/LLM-judged song corrections in ``canonical_v2.json`` (built
from typed text + resolved InputURL YouTube titles + search-result evidence +
web search) and re-buckets every Segment-A song under Andrew's RELAXED rule:

  torrent-confident : FLAC torrent (non-vinyl), seeders >= 2, filename loosely
                      matches the CORRECTED title
  community         : community karaoke version exists for the corrected name
  cmake             : real song, no acceptable torrent -> job from Spotify or
                      YouTube source (per-song source recorded)
  no-match          : ambiguous/unfindable -> "couldn't find a clear match" email
  dropped           : duplicate of another request, or excluded test requester

Read-only against prod (availability + search-standalone). Search results are
cached in ``audio_search_cache.json`` under ``search_v2`` — resumable.

Outputs: ``actions_v3.json`` + ``review_summary_v3.md`` in --out.

Usage:
  ADMIN_TOKEN=... python scripts/karaokehunt_outreach/phase1c_recanonicalize.py \
      --actions .../outreach_out/actions.json --out .../outreach_out
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# Reuse phase1b's HTTP helper, gate helpers, cache IO, and endpoints.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase1b_audio_search import (  # noqa: E402
    AVAILABILITY_ENDPOINT, SEARCH_ENDPOINT, api_post, load_cache, save_cache,
    _trim_result, norm, song_key, now_iso,
)

MIN_SEEDERS = 2  # Andrew's floor
TORRENT_PROVIDERS = {"red", "ops"}


def base_title(t: str) -> str:
    """Title without trailing parenthetical/feat, for loose matching."""
    orig = (t or "").strip()
    t = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", orig)
    t = re.sub(r"\s+(feat\.|ft\.)\s+.*$", "", t, flags=re.I)
    return t.strip() or orig


def _norm_tokens(s: str) -> list[str]:
    s = re.sub(r"[_\-.]+", " ", (s or "").lower())
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE)
    return [t for t in s.split() if t]


def loose_match(title: str, filename: str) -> bool:
    """>=60% of (base) title tokens appear in the filename."""
    toks = _norm_tokens(base_title(title))
    if not toks:
        return False
    ftoks = set(_norm_tokens(filename))
    hits = sum(1 for t in toks if t in ftoks)
    return hits / len(toks) >= 0.6


def is_torrent(r: dict) -> bool:
    return (r or {}).get("is_lossless") is True and \
        ((r or {}).get("provider") or "").lower() in TORRENT_PROVIDERS and \
        (((r or {}).get("quality_data") or {}).get("media") or "").lower() != "vinyl"


def fname(r: dict) -> str:
    tf = (r or {}).get("target_file") or ""
    return tf.split("/")[-1] if tf else ((r or {}).get("title") or "")


def relaxed_pick(results: list[dict], title: str) -> dict | None:
    """Best torrent under the relaxed rule: >=2 seeders + loose filename match.
    Highest seeders wins."""
    ok = [r for r in results if is_torrent(r) and (r.get("seeders") or 0) >= MIN_SEEDERS
          and loose_match(title, fname(r))]
    return max(ok, key=lambda r: r.get("seeders") or 0) if ok else None


def spotify_pick(results: list[dict], title: str) -> dict | None:
    """Best Spotify result whose track name loosely matches; avoid live/remix
    unless the title itself asks for one."""
    want_variant = bool(re.search(r"remix|live|acoustic", title, re.I))
    cands = []
    for r in results:
        if (r.get("provider") or "").lower() != "spotify":
            continue
        track = fname(r)
        if not loose_match(title, track):
            continue
        is_variant = bool(re.search(r"remix|- live|ao vivo|acoustic|instrumental|lofi",
                                    track, re.I))
        cands.append((is_variant != want_variant, -(r.get("match_score") or 0), r))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands[0][2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", required=True, help="phase1 actions.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    args = ap.parse_args()
    if not args.token:
        print("ERROR: need ADMIN_TOKEN", file=sys.stderr)
        return 2

    cache_path = os.path.join(args.out, "audio_search_cache.json")
    cache = load_cache(cache_path)
    v2 = json.load(open(os.path.join(args.out, "canonical_v2.json")))
    decisions, exclude_req = v2["decisions"], set(v2["exclude_requesters"])
    actions = json.load(open(args.actions))

    def corrected(k: str) -> tuple[str, str]:
        d = decisions.get(k)
        if d and d.get("artist"):
            return d["artist"], d["title"]
        c = cache.get(k, {}).get("canonical") or {}
        return c.get("artist", ""), c.get("title", "")

    # --- 1. availability re-check for corrected names (community-first/research/renamed) ---
    recheck_keys = []
    for k, d in decisions.items():
        if k not in cache:
            continue
        if d["action"] in ("community-first", "research", "keep", "cmake") and d.get("artist"):
            a, t = corrected(k)
            old = cache.get(k, {}).get("canonical") or {}
            if d["action"] == "community-first" or \
               norm(a) != norm(old.get("artist", "")) or norm(t) != norm(old.get("title", "")):
                if "recheck_v2" not in cache.get(k, {}):
                    recheck_keys.append(k)
    if recheck_keys:
        pairs = [{"artist": corrected(k)[0], "title": corrected(k)[1]} for k in recheck_keys]
        print(f"community re-check for {len(pairs)} corrected songs...", flush=True)
        resp = api_post(AVAILABILITY_ENDPOINT, {"tracks": pairs}, args.token)
        by = {(norm(r["artist"]), norm(r["title"])): r for r in resp.get("results", [])}
        for k in recheck_keys:
            a, t = corrected(k)
            r = by.get((norm(a), norm(t)), {})
            cache[k]["recheck_v2"] = {"available": bool(r.get("available")),
                                      "versions": r.get("versions", []), "at": now_iso()}
        save_cache(cache, cache_path)

    # --- 2. re-search corrected names (research + community-first fallbacks) ---
    to_search = []
    for k, d in decisions.items():
        if d["action"] == "research" or \
           (d["action"] == "community-first" and not cache[k].get("recheck_v2", {}).get("available")):
            if "search_v2" not in cache[k]:
                to_search.append(k)
    print(f"re-searching {len(to_search)} corrected songs...", flush=True)
    for i, k in enumerate(to_search):
        a, t = corrected(k)
        try:
            resp = api_post(SEARCH_ENDPOINT, {"artist": a, "title": t},
                            args.token, timeout=300, retries=1)
            results = [_trim_result(r) for r in (resp.get("results") or [])]
            cache[k]["search_v2"] = {"session_id": resp.get("search_session_id"),
                                     "results": results, "at": now_iso()}
        except Exception as e:
            cache[k]["search_v2"] = {"error": str(e)[:300], "at": now_iso()}
        save_cache(cache, cache_path)
        pick = relaxed_pick(cache[k]["search_v2"].get("results", []), t)
        verdict = (f"TORRENT ✅ {fname(pick)} ({pick.get('seeders')} seeders)"
                   if pick else "no torrent ≥2 seeders")
        print(f"[{i + 1}/{len(to_search)}] {a} – {t}: {verdict}", flush=True)

    # --- 3. final verdict per song ---
    verdicts: dict[str, dict] = {}
    for k, e in cache.items():
        d = decisions.get(k, {})
        action = d.get("action", "")
        a, t = corrected(k)
        v = {"artist": a, "title": t, "note": d.get("note", ""), "conf": d.get("conf", ""),
             "kn_brands": e.get("kn_brands")}
        srch = e.get("search") or {}
        if action == "exclude-requester":
            v["bucket"] = "dropped"; v["why"] = "test requester"
        elif action == "duplicate":
            v["bucket"] = "dropped"; v["why"] = f"duplicate of {d['dup_of']}"
        elif action == "no-match":
            v["bucket"] = "no-match"
        elif e.get("recheck", {}).get("available") or e.get("recheck_v2", {}).get("available"):
            rc = e.get("recheck_v2") if e.get("recheck_v2", {}).get("available") else e.get("recheck")
            v["bucket"] = "community"; v["versions"] = rc.get("versions", [])
        elif srch.get("pick_index") is not None and not action:
            v["bucket"] = "torrent"; v["picked"] = srch["picked"]; v["strict"] = True
        else:
            results = (e.get("search_v2") or {}).get("results") or srch.get("top_results") or []
            if srch.get("best"):
                results = results + [srch["best"]]
            pick = relaxed_pick(results, t)
            if action == "cmake":
                pick = None  # judged no-torrent; go straight to source pick
            if pick:
                v["bucket"] = "torrent"; v["picked"] = pick
            else:
                if d.get("source") == "youtube" or (action == "cmake" and d.get("url")):
                    v["bucket"] = "cmake"; v["source"] = "youtube"; v["url"] = d.get("url", "")
                else:
                    sp = spotify_pick(results, t)
                    if sp:
                        v["bucket"] = "cmake"; v["source"] = "spotify"; v["picked"] = sp
                    else:
                        v["bucket"] = "no-match"
        verdicts[k] = v

    # --- 4. rebuild per-requester actions ---
    out_actions, counts = [], {"community": 0, "torrent": 0, "cmake": 0, "no-match": 0, "dropped": 0}
    for aRow in actions:
        email = aRow["email"]
        if email in exclude_req:
            continue
        row = {"email": email, "community": list(aRow.get("community_songs", [])),
               "torrent": [], "cmake": [], "no_match": [], "dropped": [], "approved": False}
        for s in aRow.get("generate_songs", []):
            k = song_key(s["artist"], s["title"])
            v = verdicts.get(k)
            if v is None:
                row["no_match"].append({**s, "why": "unprocessed"}); continue
            entry = {**s, "canonical_artist": v["artist"], "canonical_title": v["title"],
                     "note": v.get("note", "")}
            b = v["bucket"]
            if b == "community":
                row["community"].append({**entry, "versions": v.get("versions", [])})
            elif b == "torrent":
                row["torrent"].append({**entry, "picked": v["picked"],
                                       "backing_preference": "auto" if s.get("wants_backing") else "clean"})
            elif b == "cmake":
                row["cmake"].append({**entry, "source": v["source"],
                                     "url": v.get("url", ""), "picked": v.get("picked"),
                                     "backing_preference": "auto" if s.get("wants_backing") else "clean"})
            elif b == "dropped":
                row["dropped"].append({**entry, "why": v.get("why", "")})
            else:
                row["no_match"].append(entry)
            counts[b] = counts.get(b, 0) + 1
        out_actions.append(row)

    json.dump(out_actions, open(os.path.join(args.out, "actions_v3.json"), "w"),
              indent=1, ensure_ascii=False)

    # --- 5. summary ---
    md = os.path.join(args.out, "review_summary_v3.md")
    with open(md, "w") as f:
        f.write("# Segment A — final buckets after canonicalization v2 (relaxed rule)\n\n")
        f.write(f"Generated {now_iso()}. Requesters: {len(out_actions)} "
                f"(excluded test requesters: {', '.join(sorted(exclude_req))})\n\n")
        f.write(f"- community: {counts['community']} · torrent-submit: {counts['torrent']} · "
                f"spotify/youtube-make: {counts['cmake']} · no-match: {counts['no-match']} · "
                f"dropped dup/test: {counts['dropped']}\n\n")
        for section, title in (("torrent", "Torrent submissions"),
                               ("cmake", "Spotify/YouTube makes"),
                               ("no-match", "No match (email only)"),
                               ("community", "Community (link in email)"),
                               ("dropped", "Dropped")):
            f.write(f"## {title}\n\n")
            for k, v in sorted(verdicts.items(), key=lambda kv: (kv[1]["artist"] or "").lower()):
                if v["bucket"] != section:
                    continue
                extra = ""
                if section == "torrent":
                    p = v["picked"]
                    extra = f" · {fname(p)} · {p.get('seeders')} seeders [{p.get('provider')}]"
                elif section == "cmake":
                    extra = f" · {v['source']}" + (f" · {fname(v['picked'])}" if v.get("picked") else f" · {v.get('url', '')}")
                elif section == "dropped":
                    extra = f" · {v.get('why', '')}"
                kb = v.get("kn_brands") or {}
                if section in ("torrent", "cmake") and kb.get("count"):
                    b0 = (kb.get("versions") or [{}])[0]
                    extra += (f" · ⚠️{kb['count']} BRAND karaoke version(s) exist "
                              f"(e.g. {b0.get('brand')} {b0.get('url')})")
                note = f" — {v['note']}" if v.get("note") else ""
                f.write(f"- {v['artist']} – {v['title']}{extra}{note}\n")
            f.write("\n")

    print(f"\nWROTE {md}\n      {os.path.join(args.out, 'actions_v3.json')}")
    print(f"SUMMARY: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
