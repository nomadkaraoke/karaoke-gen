#!/usr/bin/env python3
"""KaraokeHunt outreach — Phase 1 (analyze + draft, ZERO spend).

Reads the recovered KaraokeHunt track-request history (parsed from Pushbullet
"KaraokeHunt Order" notifications), cleans it, checks each requested song for an
existing community karaoke version via karaoke-gen's deployed
``POST /api/bulk/availability`` endpoint, buckets each request, and drafts a
personalized outreach email per requester.

Phase 1 creates NOTHING and sends NOTHING: no accounts, no jobs, no emails. It
only reads the request CSV + hits the (free, read-only) availability endpoint,
and writes a human-review packet + a machine-readable actions file that Phase 2
consumes once Andrew has approved rows.

Buckets (per requested song):
  - COMMUNITY  : a community karaoke version already exists -> email links it.
  - GENERATE   : no community version -> Phase 2 will create the requester's
                 account, grant credits, and submit a public karaoke-gen job.
  - SKIP       : junk / test / un-actionable row (flagged for Andrew).

Usage:
  ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens \
      --project=nomadkaraoke | cut -d',' -f1) \
  python scripts/karaokehunt_outreach/phase1_analyze.py \
      --requests /path/to/karaokehunt_track_requests.csv \
      --out ./outreach_out
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict

API_BASE = os.environ.get("KG_API_BASE", "https://api.nomadkaraoke.com")
AVAILABILITY_ENDPOINT = f"{API_BASE}/api/bulk/availability"
MAX_BATCH = 100  # server-side MAX_BULK_SONGS
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# Requesters to exclude entirely (Andrew's own test submissions + known tests).
EXCLUDE_EMAILS = {"andrew@beveridge.uk"}

CREDITS_TO_GRANT = 3  # apology-for-the-delay grant, per Andrew

LOGIN_URL = "https://gen.nomadkaraoke.com"


# --------------------------------------------------------------------------- #
# Data cleaning
# --------------------------------------------------------------------------- #
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def valid_email(e: str) -> bool:
    """Basic sanity filter — drop malformed addresses like 'a' or 'test'."""
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (e or "").strip()))


def is_junk(artist: str, title: str) -> bool:
    """Heuristic junk/test-row detector (gibberish, repeated chars, empties)."""
    a, t = norm(artist), norm(title)
    if not a and not t:
        return True
    if len(a) <= 2 and len(t) <= 2:
        return True
    if a == t and len(a) <= 4:
        return True
    stripped_a = a.replace(" ", "")
    stripped_t = t.replace(" ", "")
    if stripped_a and re.fullmatch(r"(.)\1*", stripped_a):
        return True
    if stripped_t and re.fullmatch(r"(.)\1*", stripped_t):
        return True
    if a in {"test", "ryd"} or t in {"test"}:
        return True
    return False


def valid_youtube_url(u: str) -> str:
    """Return a usable YouTube URL or '' — the InputURL field is often garbage
    (users pasted the song title in there), so only keep real youtube links."""
    u = (u or "").strip()
    if re.search(r"(youtube\.com/watch|youtu\.be/|youtube\.com/shorts)", u, re.I):
        return u
    return ""


# --------------------------------------------------------------------------- #
# Availability check (deployed endpoint, batched)
# --------------------------------------------------------------------------- #
def check_availability(pairs: list[dict], token: str) -> dict:
    """pairs: [{'artist','title'}] -> {(norm_artist,norm_title): result_dict}."""
    out: dict[tuple, dict] = {}
    for i in range(0, len(pairs), MAX_BATCH):
        chunk = pairs[i : i + MAX_BATCH]
        body = json.dumps({"tracks": chunk}).encode()
        req = urllib.request.Request(AVAILABILITY_ENDPOINT, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        # api.nomadkaraoke.com sits behind Cloudflare; the default urllib UA trips
        # the bot-signature ban (CF error 1010), so present a browser UA.
        req.add_header("User-Agent", BROWSER_UA)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise
        for r in data.get("results", []):
            out[(norm(r["artist"]), norm(r["title"]))] = r
        print(f"  availability: checked {min(i + MAX_BATCH, len(pairs))}/{len(pairs)}", flush=True)
    return out


# --------------------------------------------------------------------------- #
# Email drafting (templates; Andrew reviews/edits every one before send)
# --------------------------------------------------------------------------- #
def _song_label(s: dict) -> str:
    art = s["artist"].strip()
    tit = s["title"].strip()
    if art and tit:
        return f"“{tit}” by {art}"
    return f"“{tit or art}”"


def draft_email(email: str, community: list[dict], generate: list[dict]) -> dict:
    """Return {subject, body} personalized to this requester's songs."""
    first_song = (community + generate)[0]
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
            lines.append(
                "Good news — karaoke versions of these already exist, here they are:"
            )
        for s in community:
            urls = ", ".join(v["url"] for v in s.get("versions", []) if v.get("url"))
            brand = f" ({s['versions'][0]['brand']})" if s.get("versions") else ""
            lines.append(f"  • {_song_label(s)}: {urls or '(link in the app)'}{brand}")
        lines.append("")

    if generate:
        if len(generate) == 1:
            lines.append(
                f"And there was no karaoke version of {_song_label(generate[0])} out "
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
            for s in generate:
                lines.append(f"  • {_song_label(s)}")
        lines.append("")

    lines.append(
        f"I’ve also added {CREDITS_TO_GRANT} free credits to an account for you as an "
        f"apology for the delay, so you can make more karaoke videos of any song you like. "
        f"Just sign in with this email at {LOGIN_URL} (no password — it emails you a link)."
    )
    lines.append("")
    lines.append("Thanks for your patience, and happy singing.")
    lines.append("— Andrew, Nomad Karaoke")
    lines.append("")
    lines.append(
        "(Not interested? No problem at all — just reply and I won’t email you again.)"
    )
    return {"subject": subject, "body": "\n".join(lines)}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", required=True, help="karaokehunt_track_requests.csv")
    ap.add_argument("--out", default="./outreach_out", help="output dir")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--skip-availability", action="store_true",
                    help="skip the API call (mark all as unknown) for a dry structural run")
    args = ap.parse_args()

    if not args.token and not args.skip_availability:
        print("ERROR: need --token or ADMIN_TOKEN env (or use --skip-availability)", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    rows = list(csv.DictReader(open(args.requests)))
    print(f"loaded {len(rows)} request rows")

    # --- clean + dedupe per (email, song) ---
    excluded = skipped_junk = 0
    per_user: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        artist, title = r.get("artist", ""), r.get("title", "")
        if not email or email in EXCLUDE_EMAILS or not valid_email(email):
            excluded += 1
            continue
        if is_junk(artist, title):
            skipped_junk += 1
            continue
        key = (norm(artist), norm(title))
        song = per_user[email].get(key)
        url = valid_youtube_url(r.get("input_url", ""))
        wants_backing = str(r.get("backing_vocals", "")).strip().lower() == "true"
        if song is None:
            per_user[email][key] = {
                "artist": artist.strip(), "title": title.strip(),
                "url": url, "wants_backing": wants_backing,
                "dates": [r.get("date", "")],
            }
        else:
            song["dates"].append(r.get("date", ""))
            if url and not song["url"]:
                song["url"] = url
            song["wants_backing"] = song["wants_backing"] or wants_backing

    unique_songs = {k for songs in per_user.values() for k in songs}
    print(f"after cleaning: {len(per_user)} requesters, {len(unique_songs)} unique songs "
          f"(excluded {excluded} own/empty, {skipped_junk} junk)")

    # --- availability check (unique songs only) ---
    avail: dict[tuple, dict] = {}
    if not args.skip_availability:
        pairs = [{"artist": a, "title": t} for (a, t) in
                 {(s["artist"], s["title"]) for songs in per_user.values() for s in songs.values()}]
        print(f"checking community availability for {len(pairs)} unique songs...")
        avail = check_availability(pairs, args.token)

    # --- bucket per user + draft ---
    actions = []
    n_comm = n_gen = 0
    for email, songs in sorted(per_user.items()):
        community, generate = [], []
        for s in songs.values():
            res = avail.get((norm(s["artist"]), norm(s["title"])), {})
            s_out = {**s, "available": bool(res.get("available")),
                     "versions": res.get("versions", []), "brands": res.get("brands", [])}
            if s_out["available"]:
                community.append(s_out); n_comm += 1
            else:
                generate.append(s_out); n_gen += 1
        email_draft = draft_email(email, community, generate)
        actions.append({
            "email": email,
            "credits_to_grant": CREDITS_TO_GRANT,
            "community_songs": community,
            "generate_songs": generate,
            "email_subject": email_draft["subject"],
            "email_body": email_draft["body"],
            "approved": False,  # Andrew flips per row in Phase 2
        })

    # --- write outputs ---
    actions_path = os.path.join(args.out, "actions.json")
    json.dump(actions, open(actions_path, "w"), indent=2, ensure_ascii=False)

    packet_path = os.path.join(args.out, "review_packet.md")
    with open(packet_path, "w") as f:
        f.write("# KaraokeHunt outreach — review packet (Phase 1, nothing sent)\n\n")
        f.write(f"- Requesters: **{len(per_user)}**\n")
        f.write(f"- Unique songs: **{len(unique_songs)}**\n")
        f.write(f"- Songs with an existing community version (just link them): **{n_comm}**\n")
        f.write(f"- Songs to GENERATE (account + {CREDITS_TO_GRANT} credits + public job): **{n_gen}**\n")
        f.write(f"- Excluded (your own/empty): {excluded} rows · junk skipped: {skipped_junk} rows\n\n")
        f.write("Approve/edit rows, then run Phase 2. **No account, job, or email exists yet.**\n\n---\n\n")
        for a in actions:
            f.write(f"## {a['email']}  ·  grant {a['credits_to_grant']} credits\n\n")
            if a["community_songs"]:
                f.write("**Already has a community version (link it):**\n\n")
                for s in a["community_songs"]:
                    urls = ", ".join(v["url"] for v in s["versions"] if v.get("url")) or "(no link)"
                    f.write(f"- {_song_label(s)} → {urls}\n")
                f.write("\n")
            if a["generate_songs"]:
                f.write("**No community version — will GENERATE (public job):**\n\n")
                for s in a["generate_songs"]:
                    src = s["url"] or "(search for source)"
                    bp = "auto (keep backing)" if s["wants_backing"] else "clean (strip backing)"
                    f.write(f"- {_song_label(s)} · source: {src} · backing: {bp}\n")
                f.write("\n")
            f.write(f"**Draft email — subject:** {a['email_subject']}\n\n")
            f.write("```\n" + a["email_body"] + "\n```\n\n---\n\n")

    print(f"\nWROTE:\n  {packet_path}\n  {actions_path}")
    print(f"\nSUMMARY: {len(per_user)} requesters | {n_comm} community-link songs | "
          f"{n_gen} to-generate songs | 0 accounts/jobs/emails created (Phase 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
