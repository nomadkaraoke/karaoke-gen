#!/usr/bin/env python3
"""KaraokeHunt outreach — Phase 1b (audio-search verdicts, still ZERO spend).

Takes the Phase-1 output (``actions.json``) and, for every song in a requester's
GENERATE bucket, runs the Segment-A pipeline Andrew specified:

  1. **match-judge canonicalization** — ``POST /api/catalog/match-judge`` fixes
     typos ("bohemian rapsody" -> "Bohemian Rhapsody"). Confident verdicts
     replace the artist/title used downstream (originals are kept for the email
     wording and audit trail).
  2. **community re-check** — songs whose canonical name differs are re-checked
     against ``POST /api/bulk/availability``; hits move to the COMMUNITY bucket.
  3. **flacfetch audio search** — ``POST /api/audio-search/search-standalone``
     per remaining song (read-only: writes only a 7-day-TTL search-session doc,
     no job, no credit deduction). The confident-FLAC-from-torrent gate is a
     faithful port of ``pick_auto_selection`` from
     backend/services/audio_search_service.py (tier-1 BEST CHOICE rule):
     lossless AND seeders >= 50 AND media != vinyl AND filename matches title.
  4. **bucketing** — pick found -> CONFIDENT (Phase 2 auto-submits via the saved
     ``search_session_id`` + ``selection_index``); no pick -> NO-MATCH ("we
     couldn't find a clear match" email variant, credits only).

Nothing is created or sent. Outputs:
  - ``actions_v2.json``      — per-requester actions incl. session ids (Phase 2 input)
  - ``review_packet_v2.md``  — human review packet with per-song verdicts + drafts
  - ``audio_search_cache.json`` — per-song cache; the run is resumable/re-runnable.

⚠️ Search sessions expire after 7 DAYS — if Andrew approves later than that,
re-run this script (cached searches can be refreshed with --refresh-search).

Usage:
  ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens \
      --project=nomadkaraoke | cut -d',' -f1) \
  python scripts/karaokehunt_outreach/phase1b_audio_search.py \
      --actions /path/to/outreach_out/actions.json \
      --out /path/to/outreach_out [--limit 3]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

API_BASE = os.environ.get("KG_API_BASE", "https://api.nomadkaraoke.com")
MATCH_JUDGE_ENDPOINT = f"{API_BASE}/api/catalog/match-judge"
AVAILABILITY_ENDPOINT = f"{API_BASE}/api/bulk/availability"
SEARCH_ENDPOINT = f"{API_BASE}/api/audio-search/search-standalone"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
# /api/catalog/* is rate-limited to 20 req / 60 s per user — pace under it.
JUDGE_PACE_SECONDS = 3.2
CREDITS_TO_GRANT = 3
LOGIN_URL = "https://gen.nomadkaraoke.com"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def song_key(artist: str, title: str) -> str:
    return f"{norm(artist)}||{norm(title)}"


# --------------------------------------------------------------------------- #
# HTTP helper (browser UA required — Cloudflare WAF bans non-browser UAs)
# --------------------------------------------------------------------------- #
def api_post(url: str, body: dict, token: str, timeout: int = 120,
             retries: int = 3) -> dict:
    data = json.dumps(body).encode()
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", BROWSER_UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt < retries:
                time.sleep(10 * (attempt + 1))
                continue
            if e.code >= 500 and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code} from {url}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"unreachable: {last_err}")


# --------------------------------------------------------------------------- #
# Confident-FLAC-from-torrent gate.
#
# FAITHFUL PORT of backend/services/audio_search_service.py
# (_categorize_result / _get_best_result / _check_filename_mismatch /
# pick_auto_selection). Deliberately NOT the looser select_best ranking — this
# is the bulk-mode tier-1 gate Andrew wants: only auto-submit when the best
# result is BEST CHOICE (true lossless torrent, 50+ seeders, non-vinyl) and the
# filename matches the searched title. Keep in sync with the backend.
# --------------------------------------------------------------------------- #
_BEST_RESULT_PRIORITY = [
    "BEST CHOICE",
    "STUDIO ALBUMS",
    "HI-RES 24-BIT",
    "SINGLES",
    "COMPILATIONS",
    "SPOTIFY",
    "YOUTUBE",
    "OTHER",
]


def _categorize_result(result: Dict[str, Any]) -> str:
    is_lossless = result.get("is_lossless") is True
    quality_data = result.get("quality_data") or {}
    is_24bit = quality_data.get("bit_depth") == 24
    seeders = result.get("seeders") or 0
    provider = (result.get("provider") or "").lower()
    release_type = (result.get("release_type") or "").lower()
    media = (quality_data.get("media") or "").lower()

    if provider == "spotify":
        return "SPOTIFY"
    if provider == "youtube" or not is_lossless:
        return "YOUTUBE"
    if is_lossless and media == "vinyl":
        return "VINYL RIPS"
    if is_lossless and seeders >= 50:
        return "BEST CHOICE"
    if is_lossless and is_24bit:
        return "HI-RES 24-BIT"
    if is_lossless and (
        release_type == "live album" or release_type == "bootleg" or "live" in release_type
    ):
        return "LIVE VERSIONS"
    if is_lossless and release_type in ("compilation", "soundtrack", "anthology"):
        return "COMPILATIONS"
    if is_lossless and release_type in ("single", "ep"):
        return "SINGLES"
    if is_lossless and (release_type == "album" or not release_type):
        return "STUDIO ALBUMS"
    return "OTHER"


def _get_best_result(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not results:
        return None
    best: Optional[Dict[str, Any]] = None
    best_priority = float("inf")
    for result in results:
        category = _categorize_result(result)
        if category == "VINYL RIPS":
            continue
        try:
            priority = _BEST_RESULT_PRIORITY.index(category)
        except ValueError:
            priority = float("inf")
        if priority < best_priority:
            best = result
            best_priority = priority
        elif priority == best_priority and best is not None:
            if (result.get("seeders") or 0) > (best.get("seeders") or 0):
                best = result
    return best if best is not None else results[0]


def _check_filename_mismatch(search_title: str, result: Dict[str, Any]) -> bool:
    if len(search_title) < 3:
        return False
    target_file = result.get("target_file")
    if target_file:
        raw_filename = target_file.split("/")[-1] or target_file
        without_ext = re.sub(r"\.[^.]+$", "", raw_filename)
        filename = re.sub(r"^\d{1,3}\s*[-.\s]\s*", "", without_ext)
    elif result.get("title"):
        filename = result["title"]
    else:
        return False

    def normalize(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[_\-.]+", " ", s)
        s = re.sub(r"[^a-z0-9\s]", "", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    norm_title = normalize(search_title)
    norm_file = normalize(filename)
    if not norm_file or not norm_title:
        return False
    title_tokens = [t for t in norm_title.split() if t]
    file_tokens = set(norm_file.split())
    if not title_tokens:
        return False
    matches = all(tok in file_tokens for tok in title_tokens)
    return not matches


def pick_auto_selection(results: List[Dict[str, Any]], search_title: str = "") -> Optional[int]:
    if not results:
        return None
    if len((search_title or "").strip()) < 3:
        return None
    best = _get_best_result(results)
    if best is None:
        return None
    if _categorize_result(best) != "BEST CHOICE":
        return None
    if _check_filename_mismatch(search_title, best):
        return None
    return best.get("index")


# --------------------------------------------------------------------------- #
# Cache (per unique song, resumable)
# --------------------------------------------------------------------------- #
def load_cache(path: str) -> dict:
    if os.path.exists(path):
        return json.load(open(path))
    return {}


def save_cache(cache: dict, path: str) -> None:
    tmp = path + ".tmp"
    json.dump(cache, open(tmp, "w"), indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _trim_result(r: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the fields the gate + human review need (sessions store the
    full results server-side; we don't need to duplicate everything locally)."""
    keep = ("index", "title", "artist", "provider", "seeders", "target_file",
            "release_type", "is_lossless", "quality_data", "quality_str",
            "match_score", "year", "size_bytes")
    return {k: r.get(k) for k in keep}


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
def judge_song(entry: dict, token: str) -> None:
    """Populate entry['judge'] + entry['canonical'] via /api/catalog/match-judge."""
    artist, title = entry["artist"], entry["title"]
    verdict = api_post(MATCH_JUDGE_ENDPOINT,
                       {"artist": artist, "title": title, "stage": "full"},
                       token, timeout=60)
    entry["judge"] = {
        "kind": verdict.get("kind"),
        "confident": verdict.get("confident"),
        "canonical_artist": verdict.get("canonical_artist"),
        "canonical_title": verdict.get("canonical_title"),
        "engine": verdict.get("engine"),
        "reason": verdict.get("reason", ""),
        "at": now_iso(),
    }
    ca, ct = verdict.get("canonical_artist") or artist, verdict.get("canonical_title") or title
    if verdict.get("confident"):
        # Keep official casing even for cosmetic-only fixes (nicer emails);
        # "changed" (case-insensitive) drives the community re-check.
        entry["canonical"] = {"artist": ca, "title": ct,
                              "changed": norm(ca) != norm(artist) or norm(ct) != norm(title)}
    else:
        entry["canonical"] = {"artist": artist, "title": title, "changed": False}


def search_song(entry: dict, token: str) -> None:
    """Populate entry['search'] via /api/audio-search/search-standalone."""
    artist = entry["canonical"]["artist"]
    title = entry["canonical"]["title"]
    try:
        resp = api_post(SEARCH_ENDPOINT, {"artist": artist, "title": title},
                        token, timeout=300, retries=1)
    except Exception as e:  # record + continue; re-run retries errored entries
        entry["search"] = {"error": str(e)[:500], "at": now_iso()}
        return
    results = resp.get("results") or []
    pick = pick_auto_selection(results, title)
    picked = next((r for r in results if r.get("index") == pick), None) if pick is not None else None
    best = _get_best_result(results)
    # Near miss = a lossless non-vinyl torrent whose filename matches the title
    # but that missed BEST CHOICE only on seeders < 50. Andrew reviews every row,
    # so surface these for easy manual approval (session id + index are saved).
    near_miss = False
    if pick is None and best is not None:
        if (_categorize_result(best) in
                ("STUDIO ALBUMS", "HI-RES 24-BIT", "SINGLES", "COMPILATIONS")
                and not _check_filename_mismatch(title, best)):
            near_miss = True
    entry["search"] = {
        "near_miss": near_miss,
        "session_id": resp.get("search_session_id"),
        "results_count": resp.get("results_count", len(results)),
        "pick_index": pick,
        "picked": _trim_result(picked) if picked else None,
        "best_category": _categorize_result(best) if best else None,
        "best": _trim_result(best) if best else None,
        "top_results": [_trim_result(r) for r in results[:8]],
        "at": now_iso(),
    }


# --------------------------------------------------------------------------- #
# Email drafting (three variants merged per requester; Andrew edits every one)
# --------------------------------------------------------------------------- #
def _song_label(s: dict) -> str:
    art = (s.get("artist") or "").strip()
    tit = (s.get("title") or "").strip()
    if art and tit:
        return f"“{tit}” by {art}"
    return f"“{tit or art}”"


def draft_email(community: list, submitted: list, no_match: list) -> dict:
    first_song = (community + submitted + no_match)[0]
    subject = f"Your KaraokeHunt request for {_song_label(first_song)} — finally!"

    lines: list[str] = []
    lines.append("Hey there,")
    lines.append("")
    lines.append(
        "This is going to come a little out of the blue — a while back you "
        "requested a karaoke track through the old KaraokeHunt app, and honestly, "
        "for far too long those requests didn’t really go anywhere. I’m sorry "
        "about that. I’ve since built something much better, and I wanted to "
        "personally make good on what you asked for."
    )
    lines.append("")

    if community:
        if len(community) == 1:
            lines.append(
                f"Good news: a karaoke version of {_song_label(community[0])} already "
                "exists — here it is:"
            )
        else:
            lines.append("Good news — karaoke versions of these already exist, here they are:")
        for s in community:
            urls = ", ".join(v["url"] for v in s.get("versions", []) if v.get("url"))
            brand = f" ({s['versions'][0]['brand']})" if s.get("versions") else ""
            lines.append(f"  • {_song_label(s)}: {urls or '(link in the app)'}{brand}")
        lines.append("")

    if submitted:
        if len(submitted) == 1:
            lines.append(
                f"And there was no karaoke version of {_song_label(submitted[0])} out "
                "there yet — so I’ve just kicked off making one for you on Nomad "
                "Karaoke. You’ll get a separate email shortly asking you to quickly "
                "review the lyrics (takes a minute), and then it’ll be published."
            )
        else:
            lines.append(
                "And these didn’t have a karaoke version yet, so I’ve just kicked "
                "off making them for you on Nomad Karaoke — you’ll get a separate "
                "email shortly to quickly review the lyrics for each, then they publish:"
            )
            for s in submitted:
                lines.append(f"  • {_song_label(s)}")
        lines.append("")

    if no_match:
        if len(no_match) == 1:
            lines.append(
                f"I also saw you requested {_song_label(no_match[0])}, but I couldn’t "
                "find a clear high-quality audio match to make it from automatically. "
                f"The good news: you can make it yourself in a couple of minutes at "
                f"{LOGIN_URL} — search for the song there (or upload your own audio) "
                "and it’ll walk you through the rest."
            )
        else:
            lines.append(
                "I also saw you requested these, but I couldn’t find a clear "
                "high-quality audio match to make them from automatically — you can "
                f"make them yourself in a couple of minutes at {LOGIN_URL} (search "
                "there or upload your own audio):"
            )
            for s in no_match:
                lines.append(f"  • {_song_label(s)}")
        lines.append("")

    lines.append(
        f"I’ve also added {CREDITS_TO_GRANT} free credits to an account for you as an "
        f"apology for the delay, so you can make karaoke videos of any song you like. "
        f"Just sign in with this email at {LOGIN_URL} (no password — it emails you a link)."
    )
    lines.append("")
    lines.append("Thanks for your patience, and happy singing.")
    lines.append("— Andrew, Nomad Karaoke")
    lines.append("")
    lines.append("(Not interested? No problem at all — just reply and I won’t email you again.)")
    return {"subject": subject, "body": "\n".join(lines)}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", required=True, help="Phase-1 actions.json")
    ap.add_argument("--out", required=True, help="output dir (same as Phase 1)")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N unique songs (smoke test)")
    ap.add_argument("--refresh-search", action="store_true",
                    help="re-run searches even if cached (e.g. sessions expired)")
    args = ap.parse_args()

    if not args.token:
        print("ERROR: need --token or ADMIN_TOKEN env", file=sys.stderr)
        return 2

    actions = json.load(open(args.actions))
    cache_path = os.path.join(args.out, "audio_search_cache.json")
    cache = load_cache(cache_path)

    # --- collect unique generate songs across requesters ---
    unique: dict[str, dict] = {}
    for a in actions:
        for s in a.get("generate_songs", []):
            k = song_key(s["artist"], s["title"])
            if k not in unique:
                unique[k] = {"artist": s["artist"], "title": s["title"]}
    keys = list(unique)
    if args.limit:
        keys = keys[: args.limit]
    print(f"{len(actions)} requesters · {len(unique)} unique generate songs "
          f"(processing {len(keys)})", flush=True)

    # --- step 1: match-judge canonicalization (paced under 20/min) ---
    n_judged = 0
    for i, k in enumerate(keys):
        entry = cache.setdefault(k, dict(unique[k]))
        if "judge" in entry:
            continue
        judge_song(entry, args.token)
        n_judged += 1
        save_cache(cache, cache_path)
        c = entry["canonical"]
        tag = f" → {c['artist']} – {c['title']}" if c["changed"] else ""
        print(f"[judge {i + 1}/{len(keys)}] {entry['artist']} – {entry['title']}{tag}", flush=True)
        time.sleep(JUDGE_PACE_SECONDS)
    print(f"match-judge done ({n_judged} new)", flush=True)

    # --- step 2: community re-check for canonicalized names ---
    recheck = [k for k in keys
               if cache[k]["canonical"]["changed"] and "recheck" not in cache[k]]
    if recheck:
        pairs = [{"artist": cache[k]["canonical"]["artist"],
                  "title": cache[k]["canonical"]["title"]} for k in recheck]
        print(f"re-checking community availability for {len(pairs)} canonicalized songs...", flush=True)
        resp = api_post(AVAILABILITY_ENDPOINT, {"tracks": pairs}, args.token)
        by_key = {(norm(r["artist"]), norm(r["title"])): r for r in resp.get("results", [])}
        for k in recheck:
            c = cache[k]["canonical"]
            r = by_key.get((norm(c["artist"]), norm(c["title"])), {})
            cache[k]["recheck"] = {"available": bool(r.get("available")),
                                   "versions": r.get("versions", []),
                                   "brands": r.get("brands", []), "at": now_iso()}
        save_cache(cache, cache_path)

    # --- step 3: flacfetch search for songs still lacking a community version ---
    def needs_search(k: str) -> bool:
        e = cache[k]
        if e.get("recheck", {}).get("available"):
            return False  # became COMMUNITY after canonicalization
        if "search" not in e:
            return True
        if args.refresh_search:
            return True
        return bool(e["search"].get("error"))  # retry previous errors

    to_search = [k for k in keys if needs_search(k)]
    print(f"searching audio for {len(to_search)} songs (can take ~30-90s each)...", flush=True)
    for i, k in enumerate(to_search):
        e = cache[k]
        search_song(e, args.token)
        save_cache(cache, cache_path)
        s = e["search"]
        if s.get("error"):
            verdict = f"ERROR: {s['error'][:100]}"
        elif s["pick_index"] is not None:
            p = s["picked"]
            verdict = (f"CONFIDENT ✅ [{p['provider']}] {p.get('target_file') or p.get('title')} "
                       f"({(p.get('quality_data') or {}).get('format', '?')}, {p.get('seeders')} seeders)")
        elif s.get("near_miss"):
            b = s["best"]
            verdict = (f"NEAR MISS ({b.get('seeders')} seeders, "
                       f"{(b.get('quality_data') or {}).get('format', '?')}) — manual-approve candidate")
        else:
            verdict = f"no confident match ({s['results_count']} results, best={s['best_category']})"
        c = e["canonical"]
        print(f"[search {i + 1}/{len(to_search)}] {c['artist']} – {c['title']}: {verdict}", flush=True)

    # --- step 4: rebuild per-requester actions + drafts ---
    out_actions = []
    n_comm = n_conf = n_nomatch = n_err = 0
    for a in actions:
        community = list(a.get("community_songs", []))
        confident, no_match, errored = [], [], []
        for s in a.get("generate_songs", []):
            k = song_key(s["artist"], s["title"])
            e = cache.get(k)
            if e is None:  # not processed (e.g. --limit smoke run)
                no_match.append({**s, "pipeline": "unprocessed"})
                continue
            c = e["canonical"]
            enriched = {**s,
                        "canonical_artist": c["artist"], "canonical_title": c["title"],
                        "canonicalized": c["changed"],
                        "judge_kind": e.get("judge", {}).get("kind")}
            if e.get("recheck", {}).get("available"):
                community.append({**enriched, "artist": c["artist"], "title": c["title"],
                                  "available": True,
                                  "versions": e["recheck"]["versions"],
                                  "brands": e["recheck"]["brands"],
                                  "via": "canonicalization-recheck"})
                continue
            srch = e.get("search") or {}
            if srch.get("error"):
                errored.append({**enriched, "search_error": srch["error"]})
            elif srch.get("pick_index") is not None:
                confident.append({**enriched,
                                  "search_session_id": srch["session_id"],
                                  "selection_index": srch["pick_index"],
                                  "picked": srch["picked"],
                                  "searched_at": srch["at"],
                                  "backing_preference": "auto" if s.get("wants_backing") else "clean"})
            else:
                # Keep session id + best index so Andrew can flip a near-miss to
                # a manual submission in Phase 2 without re-searching.
                no_match.append({**enriched,
                                 "results_count": srch.get("results_count"),
                                 "best_category": srch.get("best_category"),
                                 "best": srch.get("best"),
                                 "near_miss": srch.get("near_miss", False),
                                 "search_session_id": srch.get("session_id"),
                                 "best_index": (srch.get("best") or {}).get("index"),
                                 "searched_at": srch.get("at"),
                                 "backing_preference": "auto" if s.get("wants_backing") else "clean"})
        n_comm += len(community); n_conf += len(confident)
        n_nomatch += len(no_match); n_err += len(errored)

        display_comm = community
        # Emails talk about songs using canonical names where we have them.
        subj_body = draft_email(
            display_comm,
            [{**s, "artist": s["canonical_artist"], "title": s["canonical_title"]} for s in confident],
            [{**s, "artist": s.get("canonical_artist", s["artist"]),
              "title": s.get("canonical_title", s["title"])} for s in no_match + errored],
        )
        out_actions.append({
            "email": a["email"],
            "credits_to_grant": CREDITS_TO_GRANT,
            "community_songs": community,
            "confident_songs": confident,
            "no_match_songs": no_match,
            "errored_songs": errored,
            "email_subject": subj_body["subject"],
            "email_body": subj_body["body"],
            "approved": False,
        })

    actions_path = os.path.join(args.out, "actions_v2.json")
    json.dump(out_actions, open(actions_path, "w"), indent=2, ensure_ascii=False)

    # --- review packet ---
    packet_path = os.path.join(args.out, "review_packet_v2.md")
    with open(packet_path, "w") as f:
        f.write("# KaraokeHunt outreach — review packet v2 (Phase 1b, nothing sent)\n\n")
        f.write(f"Generated {now_iso()}. **⚠️ Search sessions expire 7 days after their "
                f"`searched_at` timestamp** — approve + run Phase 2 within that window, or "
                f"re-run phase1b with `--refresh-search`.\n\n")
        n_near = sum(1 for a in out_actions for s in a["no_match_songs"] if s.get("near_miss"))
        f.write(f"- Requesters: **{len(out_actions)}**\n")
        f.write(f"- Songs with a community version (incl. found after typo-fix): **{n_comm}**\n")
        f.write(f"- CONFIDENT FLAC-from-torrent → auto-submit job on approval: **{n_conf}**\n")
        f.write(f"- No confident match → 'couldn’t find a clear match' email: **{n_nomatch}** "
                f"(of which **{n_near}** are NEAR MISSES — lossless torrent, filename matches, "
                f"just under 50 seeders; hand-approvable via saved session id + best index)\n")
        if n_err:
            f.write(f"- Search ERRORS (re-run phase1b to retry): **{n_err}**\n")
        f.write("\nApprove/edit rows in `actions_v2.json`, then run Phase 2. "
                "**No account, job, or email exists yet.**\n\n")

        f.write("## Per-song audio-search verdicts (unique songs)\n\n")
        f.write("| Song (canonical) | Typo-fixed from | Verdict | Best result |\n")
        f.write("|---|---|---|---|\n")
        for k in keys:
            e = cache[k]
            c = e["canonical"]
            fixed = f"{e['artist']} – {e['title']}" if c["changed"] else ""
            if e.get("recheck", {}).get("available"):
                verdict, best = "COMMUNITY (after typo-fix)", ""
            else:
                srch = e.get("search") or {}
                if srch.get("error"):
                    verdict, best = "ERROR", srch["error"][:80]
                elif srch.get("pick_index") is not None:
                    p = srch["picked"]
                    verdict = "✅ CONFIDENT"
                    best = (f"[{p['provider']}] {(p.get('target_file') or p.get('title') or '')[:70]} · "
                            f"{(p.get('quality_data') or {}).get('format', '?')} · {p.get('seeders')} seeders")
                elif srch:
                    verdict = "⚠️ NEAR MISS" if srch.get("near_miss") else "no match"
                    b = srch.get("best") or {}
                    best = (f"best={srch.get('best_category')} "
                            f"[{b.get('provider', '')}] "
                            f"{(b.get('target_file') or b.get('title') or '')[:60]} · "
                            f"{b.get('seeders')} seeders") if b else "0 results"
                else:
                    verdict, best = "(unprocessed)", ""
            f.write(f"| {c['artist']} – {c['title']} | {fixed} | {verdict} | {best} |\n")
        f.write("\n---\n\n")

        for a in out_actions:
            n_songs = (len(a['community_songs']) + len(a['confident_songs'])
                       + len(a['no_match_songs']) + len(a['errored_songs']))
            f.write(f"## {a['email']}  ·  grant {a['credits_to_grant']} credits · {n_songs} song(s)\n\n")
            if a["community_songs"]:
                f.write("**Community version exists (link in email):**\n\n")
                for s in a["community_songs"]:
                    urls = ", ".join(v["url"] for v in s.get("versions", []) if v.get("url")) or "(no link)"
                    via = " *(found after typo-fix)*" if s.get("via") else ""
                    f.write(f"- {_song_label(s)} → {urls}{via}\n")
                f.write("\n")
            if a["confident_songs"]:
                f.write("**CONFIDENT — will auto-submit public user-owned job on approval:**\n\n")
                for s in a["confident_songs"]:
                    p = s["picked"]
                    f.write(f"- {s['canonical_artist']} – {s['canonical_title']} · "
                            f"[{p['provider']}] {(p.get('target_file') or p.get('title') or '')[:80]} · "
                            f"{(p.get('quality_data') or {}).get('format', '?')} · "
                            f"{p.get('seeders')} seeders · backing: {s['backing_preference']}\n")
                f.write("\n")
            if a["no_match_songs"]:
                f.write("**No confident match — email says we couldn’t find it (credits only):**\n\n")
                for s in a["no_match_songs"]:
                    b = s.get("best") or {}
                    nm = ""
                    if s.get("near_miss"):
                        nm = (f" ⚠️ NEAR MISS — hand-approvable: "
                              f"[{b.get('provider')}] {(b.get('target_file') or b.get('title') or '')[:70]} · "
                              f"{b.get('seeders')} seeders (session saved)")
                    f.write(f"- {s.get('canonical_artist', s['artist'])} – "
                            f"{s.get('canonical_title', s['title'])} "
                            f"({s.get('results_count', '?')} results, best={s.get('best_category')}){nm}\n")
                f.write("\n")
            if a["errored_songs"]:
                f.write("**Search errored (treated as no-match in draft; re-run to retry):**\n\n")
                for s in a["errored_songs"]:
                    f.write(f"- {_song_label(s)}: `{s['search_error'][:100]}`\n")
                f.write("\n")
            f.write(f"**Draft email — subject:** {a['email_subject']}\n\n")
            f.write("```\n" + a["email_body"] + "\n```\n\n---\n\n")

    print(f"\nWROTE:\n  {packet_path}\n  {actions_path}\n  {cache_path}")
    print(f"\nSUMMARY: {len(out_actions)} requesters | {n_comm} community | "
          f"{n_conf} CONFIDENT auto-submit | {n_nomatch} no-match | {n_err} errored | "
          f"0 accounts/jobs/emails created (Phase 1b)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
