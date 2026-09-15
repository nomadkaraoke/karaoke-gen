#!/usr/bin/env python3
"""KaraokeHunt outreach — Segments B & C (account + 3 credits, NO generation).

Phase 1 (`phase1_analyze.py`) handles **Segment A**: KaraokeHunt *requesters*, who
get a personalized per-song email and (for songs with no community version) an
actual karaoke-gen job. This script handles the other two cohorts, who get an
account + apology credits + a "we've moved" email but **no generated track**:

  - Segment B — KaraokeHunt *app users who never requested a track*
                (registered in the old Firebase app; email = "your app is retired").
  - Segment C — *Kit-only* subscribers not in the app-user list
                (found the karaokehunt.com brochure site / stickers / GitHub / YouTube;
                email = "you showed interest online").

Priority A > B > C, so nobody is double-messaged: a Kit subscriber who was also an
app user is Segment B (or A if they requested), never C.

Like Phase 1, this DRAFTS ONLY — no account, credit, or email is created/sent.
It writes a review packet + an actions file Phase 2 consumes after Andrew approves.

⚠️ OVERLAP FLAG: Segment C is essentially the audience of the approved Frog #1
"Launch email to old mailing list" (discount-code campaign). Reconcile before send
so these people don't get two competing offers — this script just surfaces them.

Usage:
  python scripts/karaokehunt_outreach/segment_bc.py \
      --app-users   .../all_emails_deduped.csv \
      --requests    .../karaokehunt_track_requests.csv \
      --kit         .../NomadKaraoke-Kit-Subscribers-All-*.csv \
      --out         ./outreach_out
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re

EXCLUDE_EMAILS = {"andrew@beveridge.uk"}
CREDITS_TO_GRANT = 3
LOGIN_URL = "https://gen.nomadkaraoke.com"


def valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (e or "").strip()))


def load_emails(path: str, col: str = "email") -> set[str]:
    out = set()
    for r in csv.DictReader(open(path)):
        e = (r.get(col) or "").strip().lower()
        if valid_email(e) and e not in EXCLUDE_EMAILS:
            out.add(e)
    return out


def kit_active_with_names(path: str) -> dict[str, str]:
    """Return {email: first_name} for ACTIVE Kit subscribers only."""
    out = {}
    for r in csv.DictReader(open(path)):
        e = (r.get("email") or "").strip().lower()
        status = (r.get("status") or "").strip().lower()
        if valid_email(e) and e not in EXCLUDE_EMAILS and status == "active":
            out[e] = (r.get("first_name") or "").strip()
    return out


def greet(name: str) -> str:
    return f"Hey {name}," if name else "Hey there,"


def draft_segment_b(name: str) -> dict:
    body = "\n".join([
        greet(name),
        "",
        "This might come a little out of the blue. A while back you signed up for "
        "KaraokeHunt — an app I built to help people find karaoke songs. I’ve since "
        "retired KaraokeHunt and put everything into something much better: "
        "Nomad Karaoke.",
        "",
        "Instead of just *finding* karaoke tracks, Nomad Karaoke actually *generates* "
        "a proper karaoke video of almost any song you want — real synced lyrics, your "
        "choice of backing, ready to sing.",
        "",
        f"As a thank-you for being an early KaraokeHunt user (and an apology for the long "
        f"silence), I’ve set up an account for you with {CREDITS_TO_GRANT} free credits. "
        f"Just sign in with this email address at {LOGIN_URL} — no password, it emails you "
        f"a one-tap link.",
        "",
        "Hope you’ll give it a try.",
        "— Andrew, Nomad Karaoke",
        "",
        "(Not interested? No problem at all — just reply and I won’t email you again.)",
    ])
    return {"subject": "KaraokeHunt is now Nomad Karaoke — 3 free credits inside",
            "body": body}


def draft_segment_c(name: str) -> dict:
    body = "\n".join([
        greet(name),
        "",
        "This might come a little out of the blue. At some point you showed interest in "
        "KaraokeHunt — maybe through the website, a sticker, or one of my projects online. "
        "Thanks for that! I’ve since retired the KaraokeHunt name and built its much more "
        "capable successor: Nomad Karaoke.",
        "",
        "Nomad Karaoke generates a proper karaoke video of almost any song you want — real "
        "synced lyrics, your choice of backing vocals, ready to sing at your next karaoke "
        "night.",
        "",
        f"To say thanks for your interest, I’ve set up an account for you with "
        f"{CREDITS_TO_GRANT} free credits. Just sign in with this email address at "
        f"{LOGIN_URL} — no password, it emails you a one-tap link.",
        "",
        "Give it a spin — I’d love to know what you think.",
        "— Andrew, Nomad Karaoke",
        "",
        "(Not interested? No problem at all — just reply and I won’t email you again.)",
    ])
    return {"subject": "You showed interest in KaraokeHunt — it’s now Nomad Karaoke (3 free credits)",
            "body": body}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-users", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--kit", required=True)
    ap.add_argument("--out", default="./outreach_out")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    app_users = load_emails(args.app_users)
    requesters = load_emails(args.requests)
    kit = kit_active_with_names(args.kit)

    seg_b = sorted(app_users - requesters)
    seg_c = sorted(set(kit) - app_users - requesters)

    actions = []
    for email in seg_b:
        d = draft_segment_b("")  # app users have no reliable first name
        actions.append({"email": email, "segment": "B",
                        "credits_to_grant": CREDITS_TO_GRANT, "generate_songs": [],
                        "email_subject": d["subject"], "email_body": d["body"],
                        "approved": False})
    for email in seg_c:
        d = draft_segment_c(kit.get(email, ""))
        actions.append({"email": email, "segment": "C",
                        "credits_to_grant": CREDITS_TO_GRANT, "generate_songs": [],
                        "email_subject": d["subject"], "email_body": d["body"],
                        "approved": False})

    json.dump(actions, open(os.path.join(args.out, "actions_bc.json"), "w"),
              indent=2, ensure_ascii=False)

    packet = os.path.join(args.out, "review_packet_bc.md")
    with open(packet, "w") as f:
        f.write("# KaraokeHunt outreach — Segments B & C (Phase 1, nothing sent)\n\n")
        f.write(f"- **Segment B** (KaraokeHunt app users, never requested): **{len(seg_b)}** "
                f"→ account + {CREDITS_TO_GRANT} credits + 'we've moved' email, NO generation\n")
        f.write(f"- **Segment C** (Kit-only, found us online): **{len(seg_c)}** "
                f"→ account + {CREDITS_TO_GRANT} credits + 'you showed interest' email, NO generation\n\n")
        f.write("> ⚠️ Segment C overlaps the approved **Frog #1 launch email** audience — reconcile "
                "to one coordinated offer before sending.\n\n---\n\n")
        for seg, label in (("B", "Segment B — sample email (identical per recipient)"),
                           ("C", "Segment C — sample email (name-personalized)")):
            sample = next((a for a in actions if a["segment"] == seg), None)
            if sample:
                f.write(f"## {label}\n\n**Subject:** {sample['email_subject']}\n\n")
                f.write("```\n" + sample["email_body"] + "\n```\n\n")
        f.write("---\n\n## Full recipient lists\n\n")
        f.write(f"**Segment B ({len(seg_b)}):**\n\n" + ", ".join(seg_b) + "\n\n")
        f.write(f"**Segment C ({len(seg_c)}):**\n\n" + ", ".join(seg_c) + "\n\n")

    print(f"Segment B: {len(seg_b)}  |  Segment C: {len(seg_c)}  |  total: {len(seg_b)+len(seg_c)}")
    print(f"WROTE:\n  {packet}\n  {os.path.join(args.out, 'actions_bc.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
