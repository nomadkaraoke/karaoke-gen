# KaraokeHunt outreach — Phase C handoff (2026-09-19)

**Purpose:** hand a FRESH session everything needed to finish the KaraokeHunt outreach
and fully draw a line under the KaraokeHunt era. This doc is self-contained for the
remaining work; deep history lives in
[`2026-09-13-karaokehunt-outreach-handoff.md`](2026-09-13-karaokehunt-outreach-handoff.md)
(sessions 1–5 prepended chronologically) and memory `project_karaokehunt_batch_generation`.

Worktree: `/Users/andrew/Projects/nomadkaraoke/karaoke-gen-karaokehunt-outreach`
Branch: `feat/sess-20260913-0106-karaokehunt-outreach` (long-running; several PRs already
squash-merged from it — #1004 #1005 #1015 #1016; unmerged tip = docs only).

---

## 1. Mission state (UPDATED 2026-09-23 — reviews DONE, all 90 reassigned)

90 unique requested songs were generated as public jobs under holding account
**karaokehunt@nomadkaraoke.com** (all job emails → @nomadkaraoke.com catchall → Andrew's
gmail). **Final fleet state (2026-09-23):**

- **complete: 55** (live on YouTube, NOMAD-#### numbered)
- **in_review: 25** · **awaiting_review: 10** — these 35 are the skip pile (+ resubmitted
  Bhimsen 7a16be60): the REQUESTERS review them via one-click links.
- Ownership: **ALL 90 jobs reassigned to requesters; karaokehunt@ holding account is
  CLEAR.** 63 requester accounts exist (see `phasec_state.json`; <user> =
  `exists_via_interceptor_no_silent_credits` — mention credits in their email; same for
  <user>).

**ANDREW IS DONE REVIEWING (his directive, 2026-09-23):** all 15 EASY + all 26 MEDIUM
reviewed and complete. He will review NO more of these jobs. Anything still pending is
handled by the requesting users via one-click links, and **cleaned up if the requester
takes no action within 2 WEEKS of us sending their outreach email** (track send dates
per batch; cleanup = cancel/delete the parked job, account+credits stay).

Final segmentation (recompute with the snippet that produced it — session 2026-09-23):
- **Batch 1** (drafted 09-17, in Gmail drafts, UNSENT): 10 fully-resolved en requesters.
- **Batch 2 candidates**: 24 more requesters whose songs are ALL complete (incl.
  bilingual: <user> he, <user> it, <user> es, <user> es, <user> sv).
- **Skip-pile/mixed**: 29 requesters with ≥1 pending user-review job (some also have
  completed songs — ONE email each covering both, links minted at draft time).
- **No-generated-jobs**: 24 requesters (community-only / no-match variants).

Related: the **app-request interceptor is LIVE** (v0.236.0, session record
`docs/sessions/2026-Q3/2026-09-22-karaokehunt-app-interceptor.md`) — any NEW requests
from old app installs auto-convert (one freebie ever, community short-circuit,
uninstall emails). This outreach covers only the historical 90.

## 2. Where everything lives

Local PII dir (NEVER commit): `old-or-other/karaokehunt-users-export-2026-09-09/outreach_out/`
- `batch_state.json` — song → job_id → requester email(s). THE core mapping.
- `phasec_state.json` — accounts created (49; 3 credits each) + 62 job reassignments.
- `review_triage.json` — easy/medium/skip with job_ids + per-song signals.
- `actions_v3.json` — per-requester buckets (community/torrent/cmake/no_match/dropped).
- `language_inference.json` — per-requester locale+confidence (28 non-en of 89).
- earlier artifacts: canonical_v2.json, audio_search_cache.json, review packets.

Scripts (committed): `scripts/karaokehunt_outreach/` (phase1b/1c, phase2_execute,
phase_batch_generate, torrent_review).

Google Sheet (Andrew's Drive): "KaraokeHunt Outreach — Torrent Review v2 (2026-09-13)".

## 3. Verified mechanics & HARD-WON GOTCHAS

- **Admin browser login (for opening review tabs)**: in the playwright browser, on
  gen.nomadkaraoke.com run `localStorage.setItem('karaoke_access_token', '<admin token>')`
  (token: `gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1`).
  Review URL: `https://gen.nomadkaraoke.com/app/jobs#/<job_id>/review`.
  **Andrew reviews in playwright-chrome-3** (profile already logged in as admin).
- **One-click authed review links (SHIPPED + prod-verified)**:
  `POST /api/admin/users/{email}/login-link {"expiry_hours": 168, "purpose": "job_review:<job_id>"}`
  (admin token) → `{url}`. The URL shows a scanner-safe "Complete Sign-In" click, logs the
  user in, lands DIRECTLY on that job's review page. **Mint at email-draft time** (7-day
  expiry). PRs #1015 (backend) + #1016 (frontend hash-redirect fix).
- **Reassignment**: `PATCH /api/admin/jobs/{id} {"user_email": ...}` — silent,
  auto-creates account if missing (but create properly with credits FIRST).
- **Account creation**: `POST /api/users/admin/users {email, initial_credits: 3,
  credit_reason: "KaraokeHunt outreach — apology credits"}` — SILENT. 409 = exists;
  **`/api/users/admin/credits` EMAILS the user — never use it silently**
  (<user> pre-existed → got NO credits yet; mention in their email).
- **made_for_you is DUAL-ROLE**: exempts from stale-review 48h auto-expiry AND blocks
  auto-approval enforcement. All still-parked jobs have MFY=true (no expiry). To full-auto
  eval: flip MFY false → `POST /api/internal/jobs/{id}/auto-approval-eval` → re-flag.
- **Failed jobs**: `POST /api/jobs/{id}/retry` (admin) resumes from last checkpoint —
  worked for RED download failures and an encoding-worker disconnect. Tangled jobs:
  cancel + resubmit fresh.
- **NEVER scrape karaokenerds.com** — workspace `docs/KARAOKENERDS-DATA-ACCESS.md`.
- **RED IP-bans torrent-fetch bursts** (~60/hr got flacup banned ~90 min). Irrelevant now
  (downloads done) unless resubmitting.
- **EMAIL POLICY (standing)**: Andrew reviews ALL outreach emails; DRAFTS in his Gmail
  (via Gmail MCP `create_draft`); he sends in batches of ~10, waits a day, reviews
  reactions. NEVER send programmatically.

## 4. NEXT STEPS, in order

1. ~~Open the 16 remaining medium tabs~~ ✅ DONE 2026-09-20; Andrew reviewed all by 09-23.
2. ~~Reassign newly-completed jobs~~ ✅ DONE 2026-09-23 — all 90 reassigned, holding
   account clear, phasec_state.json current.
3. **Email batches** (Gmail drafts; to-addresses = requester; Andrew sends ~10/day):
   - **Batch 1 ✅ SENT by Andrew 2026-09-24 ~04:13 UTC** (all 10 confirmed in Sent,
     From andrew@nomadkaraoke.com): <user>, <user>, <user>,
     <user>, <user>, <user>, <user>, <user>,
     <user>, <user>. Batch-1 wording is APPROVED FINAL = the template.
     ➜ NEXT: monitor replies/bounces in Andrew's Gmail over the coming days.
   - **DECISION (Andrew, 2026-09-24): NO bilingual bodies** — every batch uses the
     English template; the multilingual PS strip + hosted /karaokehunt page covers
     non-English speakers. (Supersedes the earlier bilingual-body plan for batch 2.)
   - **DRAFTING CONVENTIONS (Andrew, 2026-09-23 — apply to ALL later batches):**
     (a) NO em-dashes — hyphens only, subject + body; (b) include the original
     request date: "a while back (April 1st 2025) you requested…" (dates per song in
     `actions_v3.json`; requests <~2 months old get softened wording — no "far too
     long"); (c) From = andrew@nomadkaraoke.com with his real Gmail signature.
     ⚠️ The Gmail API/MCP CANNOT set From and mangles the signature+links
     (google.com/url wrappers) — draft via the Gmail UI in playwright-chrome-3:
     Compose → From dropdown → nomadkaraoke (signature auto-inserts) → To/Subject
     via fill → body via `document.execCommand('insertText')` at collapsed-to-start
     selection (fill() wipes the signature; insertHTML is Trusted-Types-blocked).
     Sign-off "Cheers,\nAndrew" (signature block follows).
   - **ALL REMAINING SEGMENT-A EMAILS ✅ DRAFTED 2026-09-24 (77 Gmail drafts,
     verified count)**: batch 2 (24 complete), skip-pile (29, one-click links minted
     168h on 09-24 — LINKS EXPIRE ~Oct 1; if sending slips past ~Sep 28, re-mint and
     update drafts), community-only (19) + no-match (5). Content archive:
     `outreach_out/drafted_emails_2026-09-24.json`; links:
     `outreach_out/minted_links_2026-09-24.txt`.
     ✅ **ALL 77 SENT 2026-09-24 ~05:00-05:53 UTC** (Andrew approved exemplars per
     type; sent via Gmail UI in 8 batches of ~10 with ~6.5 min gaps; 0 failures;
     0 KaraokeHunt drafts remain). **SEGMENT A IS COMPLETE: all 87 requester
     outreach emails sent.** 2-week inaction cleanup clock for the 35 parked
     review jobs runs to **~2026-10-08**; review links expire ~Oct 1 (re-mint
     on request if someone replies late).
     ⚠️ DRAFTING GOTCHA: rapid scripted composes get their saves silently DROPPED by
     Gmail — always wait for the compose header's "Draft saved" ack before Save &
     close, and verify the drafts count grew after each batch.
   - **Skip-pile batches (29 requesters, 35 pending jobs)**: "we made a first draft —
     review the lyrics yourself with one click" variant. MINT the per-job one-click link
     at draft time (`job_review:<their job_id>`, 168h). Users get their 3 credits
     mentioned. Requesters with BOTH complete + pending songs (<user>,
     <user>, <user>, <user>, <user>) get ONE combined email.
   - **⏲ 2-WEEK CLEANUP RULE (Andrew, 2026-09-23)**: any job still unactioned by its
     requester 2 weeks after their email was SENT → clean up (cancel/delete the parked
     job; accounts + credits stay). Track send date per batch.
   - **No-match-only + community-only requesters** (from actions_v3: no_match rows and
     requesters whose only songs were community links) — "couldn't find a clear match /
     here's the existing version" variants + credits.
   - After each batch: check replies/bounces in Andrew's Gmail before the next.
   - **ENGAGEMENT TRACKING — Andrew's Gmail send-as relays via POSTMARK SMTP, so
     opens AND link clicks ARE tracked** (server token = Secret Manager
     `postmark-server-token`; feeds: `/messages/outbound/opens` + `/clicks`,
     filter Recipient against the 87). Also server-side: (a) logins:
     `gen_users/{email}.last_login_at`; (b) parked-job status flips in `jobs`;
     (c) `total_jobs_created`; (d) /karaokehunt views: CF zone analytics;
     (e) replies/bounces in Gmail. First hours (by ~11:15 UTC 09-24): 28/87
     opened, 6 link clicks, first full conversion <user> (clicked 06:43 UTC,
     BABYMONSTER FOREVER reviewed → YouTube by 06:59, ~1h after send).
4. ~~Hosted localized letter page~~ ✅ SHIPPED 2026-09-23 (PR #1044, v0.238.1):
   **https://gen.nomadkaraoke.com/karaokehunt** — bare URL locale-redirects, page has
   language switcher, all 33 locales. **Andrew: include the link in ALL emails** (even
   guessed-English recipients) as a PS line before the signature:
   a two-line PS before the signature (batch 1 already has it):
   "PS: You can read this note in your own language at
   https://gen.nomadkaraoke.com/karaokehunt" + a native-language strip
   "Español · Português · Français · Italiano · Svenska · Filipino · Bahasa
   Indonesia · Tiếng Việt · 한국어 · 中文 · हिन्दी · العربية · עברית · +19 more"
   (the inferred requester languages, so recipients recognise theirs).
5. **Segments B & C** (after Segment A settles): B = 266 app-users-never-requested,
   C = 37 kit-only. Drafts exist from phase 1 (`outreach_out/review_packet_bc.md` — needs
   refresh with final wording + credits). ⚠️ **C overlaps Frog #1 launch-email** —
   reconcile to ONE offer with Andrew before sending C.
6. **Draw the line under KaraokeHunt** (final cleanup):
   - All 90 jobs at final state + reassigned; karaokehunt@ holding account left as
     archive (or zero its credits).
   - GCP **Phase E is ungated**: `gcloud projects delete projectbread-karaokay` + close
     billing acct `0123B3-AAC0D8-4344D3` (needs Andrew's auth'd gcloud; ask him).
   - Play/App Store: apps already unpublished (do NOT "Delete app" on Play — keeps
     analytics).
   - BACKLOG: `/backlog-finish` Frog #2 "KaraokeHunt order-email recovery + app
     redirect" when emails are flowing; move to CHANGELOG.
   - Memory: update `project_karaokehunt_batch_generation` + MEMORY.md when done.

## 5. Loose ends / known issues

- 3 pre-existing gen users among requesters (see `outreach_out/existing_gen_users.txt`,
  local): no silent credit path — handle in their emails (offer to add credits on reply,
  or Andrew grants via admin UI accepting the notification email).
- Post-deploy canary (happy-path E2E) was flaky/failing on some main runs during batch
  load; PyPI publish failed on the 0.225.7 run — another session was on CI/errors.
- 2 cancelled jobs are expected (Emily King CF-524 duplicate; tangled Bhimsen original).
- Multi-song requesters not yet fully resolved (e.g. <user> ~15 songs, <user>,
  <user>, <user>, hello@thepropervolume) — email each ONCE when their
  whole set resolves; their skip-pile songs get one-click links in the same email.
