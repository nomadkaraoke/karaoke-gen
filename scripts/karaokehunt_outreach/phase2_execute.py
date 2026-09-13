#!/usr/bin/env python3
"""KaraokeHunt outreach — Phase 2 (EXECUTE: accounts + credits + jobs).

Consumes ``actions_v2.json`` (from phase1b) and, for every row Andrew has
flipped to ``"approved": true``:

  1. Creates the requester's account with 3 starter credits
     (``POST /api/users/admin/users`` — silent, no email; 409 = user already
     exists -> credits are NOT granted automatically, because the only
     grant endpoint for existing users, ``/api/users/admin/credits``, SENDS a
     notification email. Use ``--grant-existing`` to allow that knowingly.)
  2. For each song in ``confident_songs`` (plus any ``no_match_songs`` Andrew
     marked ``"manual_approve": true`` — near-misses), submits a PUBLIC,
     USER-OWNED job:
       impersonate -> fresh search-standalone AS THE USER -> re-locate the
       reviewed result (provider + target_file must match the reviewed pick)
       -> POST /api/jobs/create-from-search with the user's session token.
     The fresh search is required: phase1b's sessions belong to the admin
     token and create-from-search 403s if another user consumes them. The
     re-locate step guarantees we only ever submit the exact result Andrew
     reviewed — if it's gone from the fresh results, the song is SKIPPED and
     reported, never substituted.

  NOTE: create-from-search deducts 1 credit from the user, so job-submitted
  requesters end with 2 of their 3 credits. Flagged in the report.

Outreach EMAILS are deliberately NOT sent by this script — Andrew reviews and
sends those separately.

DRY-RUN BY DEFAULT: prints the full plan and exits. Pass ``--execute`` to act.
Progress is checkpointed to ``phase2_state.json`` so re-runs never double-create
accounts, double-grant credits, or double-submit jobs.

Usage:
  ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens \
      --project=nomadkaraoke | cut -d',' -f1) \
  python scripts/karaokehunt_outreach/phase2_execute.py \
      --actions /path/to/outreach_out/actions_v2.json \
      --out /path/to/outreach_out [--execute] [--only email@x.com]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

API_BASE = os.environ.get("KG_API_BASE", "https://api.nomadkaraoke.com")
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
CREDITS_TO_GRANT = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_post(path: str, body: dict, token: str, timeout: int = 300,
             ok_statuses: tuple = ()) -> tuple[int, dict]:
    """POST helper. Returns (status, parsed_json). Raises unless status is 2xx
    or listed in ok_statuses (e.g. 409 for idempotent create)."""
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", BROWSER_UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:400]
        except Exception:
            pass
        if e.code in ok_statuses:
            try:
                return e.code, json.loads(detail) if detail else {}
            except json.JSONDecodeError:
                return e.code, {"detail": detail}
        raise RuntimeError(f"HTTP {e.code} from {path}: {detail}") from e


def load_state(path: str) -> dict:
    if os.path.exists(path):
        return json.load(open(path))
    return {}


def save_state(state: dict, path: str) -> None:
    tmp = path + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def result_matches(reviewed: dict, fresh: dict) -> bool:
    """The fresh result must be the exact release Andrew reviewed."""
    if (fresh.get("provider") or "") != (reviewed.get("provider") or ""):
        return False
    rt, ft = reviewed.get("target_file"), fresh.get("target_file")
    if rt and ft:
        return rt == ft
    return (reviewed.get("title") or "") == (fresh.get("title") or "")


def submit_song(email: str, song: dict, reviewed_pick: dict, admin_token: str,
                report: list) -> Optional[str]:
    """Impersonate, re-search, re-locate reviewed pick, create job. Returns job_id."""
    artist = song.get("canonical_artist") or song["artist"]
    title = song.get("canonical_title") or song["title"]

    _, imp = api_post(f"/api/admin/users/{email}/impersonate", {}, admin_token)
    user_token = imp["session_token"]

    _, search = api_post("/api/audio-search/search-standalone",
                         {"artist": artist, "title": title}, user_token)
    fresh = search.get("results") or []
    match = next((r for r in fresh if result_matches(reviewed_pick, r)), None)
    if match is None:
        report.append(f"SKIP {email} · {artist} – {title}: reviewed result "
                      f"[{reviewed_pick.get('provider')}] "
                      f"{reviewed_pick.get('target_file') or reviewed_pick.get('title')} "
                      f"not present in fresh search ({len(fresh)} results) — NOT substituted")
        return None

    _, job = api_post("/api/jobs/create-from-search", {
        "search_session_id": search["search_session_id"],
        "selection_index": match["index"],
        "artist": artist,
        "title": title,
        "is_private": False,
        "review_mode": "auto",
        "backing_preference": song.get("backing_preference", "clean"),
    }, user_token)
    job_id = job.get("job_id") or job.get("id")
    report.append(f"OK   {email} · {artist} – {title}: job {job_id} "
                  f"([{match.get('provider')}] {match.get('target_file') or match.get('title')})")
    return job_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", required=True, help="actions_v2.json from phase1b")
    ap.add_argument("--out", required=True, help="output dir (state + report)")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--execute", action="store_true",
                    help="actually create accounts/jobs (default: dry-run plan)")
    ap.add_argument("--only", default="", help="process a single email (testing)")
    ap.add_argument("--grant-existing", action="store_true",
                    help="grant credits to ALREADY-EXISTING users via /admin/credits "
                         "(⚠️ that endpoint emails the user a credits notification)")
    args = ap.parse_args()

    if not args.token:
        print("ERROR: need --token or ADMIN_TOKEN env", file=sys.stderr)
        return 2

    actions = json.load(open(args.actions))
    state_path = os.path.join(args.out, "phase2_state.json")
    state = load_state(state_path)

    rows = [a for a in actions if a.get("approved")]
    if args.only:
        rows = [a for a in rows if a["email"] == args.only.lower()]
    if not rows:
        print("No approved rows found. Flip \"approved\": true in actions_v2.json "
              "(and optionally \"manual_approve\": true on near-miss songs) first.")
        return 0

    # --- plan ---
    plan: List[dict] = []
    for a in rows:
        submits = list(a.get("confident_songs", []))
        manual = [s for s in a.get("no_match_songs", []) if s.get("manual_approve")]
        for s in manual:
            if not s.get("best"):
                print(f"WARN: {a['email']} manual_approve song "
                      f"{s['artist']} – {s['title']} has no 'best' result; skipping")
                continue
            submits.append({**s, "picked": s["best"]})
        plan.append({"email": a["email"], "submits": submits})

    n_jobs = sum(len(p["submits"]) for p in plan)
    print(f"PLAN: {len(plan)} approved requesters · {n_jobs} job submissions · "
          f"{CREDITS_TO_GRANT} credits each\n")
    for p in plan:
        st = state.get(p["email"], {})
        acct = st.get("account") or "will create (+3 credits, silent)"
        print(f"  {p['email']}  [account: {acct}]")
        for s in p["submits"]:
            done = st.get("jobs", {}).get(f"{s['artist']}||{s['title']}")
            pk = s["picked"]
            tag = f"already submitted: {done}" if done else "will submit"
            print(f"    - {s.get('canonical_artist', s['artist'])} – "
                  f"{s.get('canonical_title', s['title'])} · [{pk.get('provider')}] "
                  f"{(pk.get('target_file') or pk.get('title') or '')[:70]} · {tag}")
    print()

    if not args.execute:
        print("DRY RUN — nothing created. Re-run with --execute to act.")
        return 0

    # --- execute ---
    report: List[str] = []
    for p in plan:
        email = p["email"]
        st = state.setdefault(email, {})

        if not st.get("account"):
            status, resp = api_post("/api/users/admin/users",
                                    {"email": email, "initial_credits": CREDITS_TO_GRANT,
                                     "credit_reason": "KaraokeHunt outreach — apology credits"},
                                    args.token, ok_statuses=(409,))
            if status == 409:
                st["account"] = "existing"
                st["credits_granted"] = False
                report.append(f"EXISTING USER {email}: account already exists — credits NOT "
                              f"granted (would email them; use --grant-existing to override)")
                if args.grant_existing:
                    api_post("/api/users/admin/credits",
                             {"email": email, "amount": CREDITS_TO_GRANT,
                              "reason": "KaraokeHunt outreach — apology credits"},
                             args.token)
                    st["credits_granted"] = True
                    report.append(f"GRANTED {CREDITS_TO_GRANT} credits to existing user {email} "
                                  f"(⚠️ notification email was sent by the API)")
            else:
                st["account"] = "created"
                st["credits_granted"] = True
                report.append(f"CREATED {email} with {CREDITS_TO_GRANT} credits (silent)")
            st["account_at"] = now_iso()
            save_state(state, state_path)

        jobs = st.setdefault("jobs", {})
        for s in p["submits"]:
            k = f"{s['artist']}||{s['title']}"
            if jobs.get(k):
                continue
            try:
                job_id = submit_song(email, s, s["picked"], args.token, report)
            except Exception as e:
                report.append(f"ERROR {email} · {s['artist']} – {s['title']}: {e}")
                job_id = None
            if job_id:
                jobs[k] = job_id
            save_state(state, state_path)
            time.sleep(2)  # gentle pacing between submissions

    report_path = os.path.join(args.out, f"phase2_report_{now_iso()[:19].replace(':', '')}.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"\nWROTE {report_path}\nState: {state_path}")
    print("\nREMINDERS: outreach emails are NOT sent by this script; "
          "job-submitted users now have 2 of 3 credits left (job cost 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
