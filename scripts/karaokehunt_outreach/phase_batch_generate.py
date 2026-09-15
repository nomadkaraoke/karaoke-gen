#!/usr/bin/env python3
"""KaraokeHunt outreach — batch generation under karaokehunt@nomadkaraoke.com.

Submits every torrent-confident and Spotify/YouTube-make song from
``actions_v3.json`` as a PUBLIC karaoke-gen job owned by the holding account
``karaokehunt@nomadkaraoke.com`` (all job emails go there — Andrew's catchall),
then immediately PATCHes ``made_for_you: true`` so the stale-review processor
never auto-expires them. Jobs are reassigned to the real requesters later
(Phase C), only once each reaches its final stage.

Per song:
  torrent        : fresh search-standalone AS karaokehunt@ -> exact
                   provider+target_file re-match of the reviewed pick (skip,
                   never substitute) -> POST /api/jobs/create-from-search
  cmake spotify  : fresh search -> match the reviewed Spotify result (fallback:
                   best title-matching Spotify result) -> create-from-search
  cmake youtube  : POST /api/jobs/create-from-url with the reviewed URL

All jobs: is_private=false, review_mode="auto" (full-auto publish when
confident), backing_preference from the original request.

One job per unique song — multi-requester songs record all requesters in the
state file for Phase C. DRY-RUN by default; ``--execute`` to act. Progress in
``batch_state.json`` (idempotent re-runs). ``--limit N`` for a canary.

Usage:
  ADMIN_TOKEN=... python scripts/karaokehunt_outreach/phase_batch_generate.py \
      --actions .../outreach_out/actions_v3.json --out .../outreach_out \
      [--execute] [--limit 3] [--wave-size 10] [--wave-interval 300]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase2_execute import api_post  # noqa: E402

HOLDING_ACCOUNT = "karaokehunt@nomadkaraoke.com"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def fname(r: dict) -> str:
    tf = (r or {}).get("target_file") or ""
    return tf.split("/")[-1] if tf else ((r or {}).get("title") or "")


def result_matches(reviewed: dict, fresh: dict) -> bool:
    if norm(fresh.get("provider")) != norm(reviewed.get("provider")):
        return False
    rt, ft = reviewed.get("target_file"), fresh.get("target_file")
    if rt and ft:
        return rt == ft
    return norm(reviewed.get("title")) == norm(fresh.get("title"))


def spotify_fallback(results: list, title: str) -> dict | None:
    """Reviewed Spotify pick missing from fresh results -> best loose title match."""
    toks = [t for t in re.sub(r"[^\w\s]", "", title.lower()).split() if t]
    best = None
    for r in results:
        if (r.get("provider") or "").lower() != "spotify":
            continue
        track = re.sub(r"[^\w\s]", "", fname(r).lower())
        hits = sum(1 for t in toks if t in track.split())
        if toks and hits / len(toks) >= 0.6:
            if best is None or (r.get("match_score") or 0) > (best.get("match_score") or 0):
                best = r
    return best


def load_state(p):
    return json.load(open(p)) if os.path.exists(p) else {}


def save_state(s, p):
    tmp = p + ".tmp"
    json.dump(s, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", required=True, help="actions_v3.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="submit only first N (canary)")
    ap.add_argument("--wave-size", type=int, default=10)
    ap.add_argument("--wave-interval", type=int, default=300, help="seconds between waves")
    args = ap.parse_args()
    if not args.token:
        print("ERROR: need ADMIN_TOKEN", file=sys.stderr)
        return 2

    actions = json.load(open(args.actions))
    state_path = os.path.join(args.out, "batch_state.json")
    state = load_state(state_path)

    # --- collect unique songs (one job per song; track all requesters) ---
    units: dict[str, dict] = {}
    for row in actions:
        for kind in ("torrent", "cmake"):
            for s in row.get(kind, []):
                a, t = s["canonical_artist"], s["canonical_title"]
                k = f"{norm(a)}||{norm(t)}"
                u = units.setdefault(k, {
                    "kind": kind, "artist": a, "title": t,
                    "backing_preference": s.get("backing_preference", "clean"),
                    "source": s.get("source", ""), "url": s.get("url", ""),
                    "picked": s.get("picked"), "requesters": [],
                })
                u["requesters"].append(row["email"])
                # any requester wanting backing wins
                if s.get("backing_preference") == "auto":
                    u["backing_preference"] = "auto"

    keys = list(units)
    if args.limit:
        keys = keys[: args.limit]
    done = [k for k in keys if state.get(k, {}).get("job_id")]
    todo = [k for k in keys if not state.get(k, {}).get("job_id")]
    print(f"{len(units)} unique songs · processing {len(keys)} · "
          f"already submitted {len(done)} · to submit {len(todo)}", flush=True)

    if not args.execute:
        for k in todo:
            u = units[k]
            how = u["url"] if u.get("source") == "youtube" else \
                (f"spotify: {fname(u['picked'] or {})}" if u["kind"] == "cmake"
                 else f"torrent: {fname(u['picked'] or {})} ({(u['picked'] or {}).get('seeders')}s)")
            print(f"  would submit [{u['kind']}] {u['artist']} – {u['title']} · {how} · "
                  f"backing={u['backing_preference']} · for {','.join(u['requesters'])}")
        print("\nDRY RUN — nothing created. Re-run with --execute.")
        return 0

    def impersonate() -> str:
        _, imp = api_post(f"/api/admin/users/{HOLDING_ACCOUNT}/impersonate", {}, args.token)
        return imp["session_token"]

    report = []
    for wave_start in range(0, len(todo), args.wave_size):
        wave = todo[wave_start: wave_start + args.wave_size]
        wave_no = wave_start // args.wave_size + 1
        print(f"\n=== wave {wave_no} ({len(wave)} songs) ===", flush=True)
        user_token = impersonate()
        for k in wave:
            u = units[k]
            a, t = u["artist"], u["title"]
            entry = state.setdefault(k, {"artist": a, "title": t,
                                         "requesters": u["requesters"], "kind": u["kind"]})
            try:
                if u.get("source") == "youtube" and u.get("url"):
                    _, job = api_post("/api/jobs/create-from-url", {
                        "url": u["url"], "artist": a, "title": t,
                        "is_private": False, "review_mode": "auto",
                        "backing_preference": u["backing_preference"],
                    }, user_token)
                else:
                    _, search = api_post("/api/audio-search/search-standalone",
                                         {"artist": a, "title": t}, user_token)
                    results = search.get("results") or []
                    match = next((r for r in results
                                  if result_matches(u["picked"] or {}, r)), None)
                    if match is None and u["kind"] == "cmake":
                        match = spotify_fallback(results, t)
                    if match is None:
                        msg = (f"SKIP {a} – {t}: reviewed pick "
                               f"{fname(u['picked'] or {})} not in fresh results "
                               f"({len(results)}) — not substituted")
                        print(f"  {msg}", flush=True)
                        report.append(msg)
                        entry["skip"] = msg
                        save_state(state, state_path)
                        continue
                    _, job = api_post("/api/jobs/create-from-search", {
                        "search_session_id": search["search_session_id"],
                        "selection_index": match["index"],
                        "artist": a, "title": t,
                        "is_private": False, "review_mode": "auto",
                        "backing_preference": u["backing_preference"],
                    }, user_token)
                job_id = job.get("job_id") or job.get("id")
                entry["job_id"] = job_id
                entry["submitted_at"] = now_iso()
                # Exempt from stale-review auto-expiry immediately. A PATCH
                # failure must not mask the successful job creation — record
                # it so a later pass can re-flag.
                try:
                    st, _ = api_post(f"/api/admin/jobs/{job_id}",
                                     {"made_for_you": True}, args.token, method="PATCH")
                    entry["made_for_you_set"] = (st == 200)
                except Exception as pe:
                    entry["made_for_you_set"] = False
                    entry["mfy_error"] = str(pe)[:200]
                msg = (f"OK   {a} – {t}: job {job_id} "
                       f"[{u['kind']}] mfy={'✅' if entry['made_for_you_set'] else '❌'}")
                print(f"  {msg}", flush=True)
                report.append(msg)
            except Exception as e:
                msg = f"ERROR {a} – {t}: {str(e)[:300]}"
                print(f"  {msg}", flush=True)
                report.append(msg)
                entry["error"] = str(e)[:300]
            save_state(state, state_path)
            time.sleep(3)
        if wave_start + args.wave_size < len(todo):
            print(f"  wave {wave_no} done — sleeping {args.wave_interval}s", flush=True)
            time.sleep(args.wave_interval)

    ok = sum(1 for k in keys if state.get(k, {}).get("job_id"))
    print(f"\nDONE: {ok}/{len(keys)} submitted. State: {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
