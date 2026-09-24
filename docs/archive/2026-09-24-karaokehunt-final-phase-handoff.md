# KaraokeHunt outreach — FINAL PHASE handoff (2026-09-24)

**Purpose:** Segment A is COMPLETE. This doc hands a fresh session everything needed
for the remaining KaraokeHunt-era work: reply/engagement monitoring, the Oct-8 cleanup
sweep, Segments B & C, and the final line-drawing (GCP Phase E, /backlog-finish).
Deep history: [`2026-09-19-karaokehunt-outreach-phase-c-handoff.md`](2026-09-19-karaokehunt-outreach-phase-c-handoff.md)
(now fully ✅ through step 3) and memory `project_karaokehunt_batch_generation`.

Worktree: `/Users/andrew/Projects/nomadkaraoke/karaoke-gen-karaokehunt-outreach`
Branch: `feat/sess-20260913-0106-karaokehunt-outreach` (docs-only tip; PRs #1004 #1005
#1015 #1016 + letter-page #1044 all merged/deployed).

## 0. UPDATE 2026-09-24 (later session) — Segments B + C SENT

- **Segment B SENT 238/238** (11:05 local, 0 errors): KaraokeHunt APP registrants who never
  requested (NOT Kit form signups). 266 → 238 after dropping 17 test/disposable/typo, 10 who
  DELETED their KH account (`karaokehunt_account_deletions.csv` — check this for any future
  send!), 1 existing Gen user (<user>@). All 238 got accounts + 3 credits SILENTLY first
  (`POST /api/users/admin/users`, 0 conflicts). Andrew approved a test at andrew@beveridge.uk.
- **Segment C SENT 35/35** = Frog #1 launch email (Andrew: fold C into Frog #1). Kit list is
  mostly app users bulk-imported 2026-04-23 (301/348 rows) — so Frog #1's audience is ONLY these
  35 genuine form signups; the rest already got A/B. CTA = vanity referral link
  `nomadkaraoke.com/r/thankyou50` (50% off 90d, attaches on next sign-in; created via
  `POST /api/referrals/admin/vanity`). Andrew's edit: "(original song, not a cover band)".
- **Letter pages** (all 33 locales, shared `components/karaokehunt/KaraokeHuntLetter`):
  `/karaokehunt` (A), `/karaokehuntlist` (B, PR #1045), `/karaokehuntnews` (C, PR #1046 + #1047).
- **SENDING METHOD (use this, not Gmail UI)**: Postmark API, **`outbound` stream**, HTML = Gmail
  structure + Andrew's real signature captured from a Segment A message
  (`outreach_out/segment_b_signature.{html,txt}`). This is the SAME path Segment A took (Gmail
  send-as relays via Postmark SMTP → outbound). **Do NOT use the `broadcast` stream**: it forces a
  Postmark unsubscribe footer + List-Unsubscribe header (Andrew disliked it — lands in his
  unsubscribe filter); Custom unsubscribe handling needs Postmark support. Opt-out = "just reply".
  Scripts: `outreach_out/segment_{b,c}_email.py`, `segment_b_run.py` (idempotent, paced 20/3min);
  state in `segment_{b,c}_state.json`. urllib needs a curl User-Agent (Cloudflare WAF 1010).
- Engagement snapshot 09-24 ~12:00Z (Segment A): 30/87 opened, 7 clicked, 0 replies/bounces;
  <user> + <user> clicked review links but never pressed "Complete Sign-In".
- Segment A recipient <user>@ had previously DELETED their KH account (missed check).
- Remaining: engagement/replies for A+B+C → ~Oct 1 link re-mints → Oct 8 cleanup → Phase E
  (Andrew: wait until after Oct 8) → `/backlog-finish` Frog #2 AND Frog #1.

---

## 1. State (2026-09-24, after sends)

- **ALL 87 Segment-A outreach emails SENT 2026-09-24** (04:12–05:53 UTC): batch 1
  (10) clicked Send by Andrew; remaining 77 sent by agent via Gmail UI (Andrew
  approved one exemplar per message type, then authorized sending in 8 paced
  batches of ~10 with ~6.5 min gaps). 0 failures; 0 KaraokeHunt drafts remain.
- All 90 generated jobs belong to their requesters; **karaokehunt@ holding account
  CLEAR**. 55 complete/on YouTube; 35 parked awaiting requester review (34 skip-pile
  + resubmitted Bhimsen 7a16be60). 1 already converted post-send (<user> →
  BABYMONSTER FOREVER → NOMAD-1705 live ~06:59 UTC, 16 min after her click).
- **Hosted localized letter LIVE**: https://gen.nomadkaraoke.com/karaokehunt
  (PR #1044, v0.238.1; bare URL locale-redirects; 33 locales; ThemeToggle localized;
  no-JS fallback). Linked from every email's PS + native-language strip.
- **App-request interceptor LIVE** (v0.236.0) — new requests from old installs
  auto-convert; nothing ongoing to do there.
- Early engagement (~11:15 UTC): **28/87 opened, 6 link clicks, 1 full conversion**.

## 2. Deadlines / clocks

- **~Oct 1**: the 35 one-click review links EXPIRE (minted 2026-09-24 ~04:00 UTC,
  168h = API max). If a recipient engages late, RE-MINT + reply/resend:
  `POST /api/admin/users/{email}/login-link {"expiry_hours":168,"purpose":"job_review:<job_id>"}`
  (admin token from `admin-tokens` secret). Per-job links: `outreach_out/minted_links_2026-09-24.txt`.
- **~Oct 8 (2 weeks after send)**: CLEANUP SWEEP — Andrew's rule: parked jobs whose
  requester took no action get cancelled/deleted (accounts + credits STAY). Check
  `jobs` status + `gen_users.last_login_at` per recipient before deleting; record
  outcomes in `phasec_state.json`.

## 3. Engagement / reply monitoring (the "check engagement" recipe)

Andrew's Gmail send-as relays via **Postmark SMTP → opens AND link clicks ARE
tracked** (do NOT assume plain Gmail = untracked; that was corrected 09-24).
- Postmark server token: Secret Manager `postmark-server-token`.
- Opens: `GET https://api.postmarkapp.com/messages/outbound/opens?count=500&offset=N`
  · Clicks: `.../clicks` — filter `Recipient` against the 87 (recipient list =
  `outreach_out/drafted_emails_2026-09-24.json` + the 10 batch-1 addresses in the
  phase-c handoff). Timestamps are EDT (-04:00).
- Server-side: `gen_users/{email}.last_login_at` (docs keyed by EMAIL);
  parked-job status flips in `jobs` (fleet ids in `batch_state.json`);
  `total_jobs_created` for credit usage; /karaokehunt views via CF zone analytics.
- Replies/bounces: Andrew's Gmail (`in:inbox subject:KaraokeHunt` etc.). Triage:
  - **<user> / <user> reply for credits** → their
    emails promised 3 credits on reply; grant via admin (the `/api/users/admin/credits`
    endpoint EMAILS the user — fine once they've replied) or Andrew via admin UI.
  - **Vague-request replies** (<user>=Justin Bieber song?, <user>=Arijit
    song?, <user>="My songs") → they name a song → generate it for them (these are
    legit requested songs, not "popular songs").
  - Bounced addresses → note in phasec_state, nothing else owed.

## 4. NEXT STEPS, in order

1. **Daily-ish engagement check + reply triage** (§3) until ~Oct 8.
2. **Oct 8 cleanup sweep** (§2). After it: zero/archive the karaokehunt@ account.
3. **Segment B (266 app-users-never-requested) & C (37 kit-buyers-only)**:
   - Base drafts exist in `outreach_out/review_packet_bc.md` — REFRESH to the final
     approved template (hyphens, Cheers/Andrew+signature, PS + language strip,
     /karaokehunt link, credits offer). These are generic (no per-user songs).
   - ⚠️ **C overlaps Frog #1 launch-email — reconcile to ONE offer with Andrew
     BEFORE drafting C.**
   - ⚠️ **Volume decision needed from Andrew for B**: 266 emails at ~10/day via
     Gmail UI = ~4 weeks. Options: bigger daily batches, or send B via Postmark
     broadcast stream (they're generic letters, not personal apologies) — ask him.
4. **GCP Phase E** (destructive — get Andrew's explicit go, his gcloud is authed):
   `gcloud projects delete projectbread-karaokay` + close billing acct
   `0123B3-AAC0D8-4344D3`. Play/App Store: apps unpublished; do NOT "Delete app".
5. **Close out**: `/backlog-finish` Frog #2 ("KaraokeHunt order-email recovery + app
   redirect") → CHANGELOG; update memory `project_karaokehunt_batch_generation` +
   MEMORY.md; `/cleanup` the outreach + letter worktrees when truly done.

## 5. HARD-WON GOTCHAS (email automation via Gmail UI)

- **Drafting/sending MUST go through the Gmail UI in playwright-chrome-3** (his
  logged-in profile). The Gmail API/MCP cannot set the From alias and mangles
  signature + links (google.com/url wrappers).
- Compose flow: Compose → From dropdown → andrew@nomadkaraoke.com (Gmail inserts his
  real signature) → fill To/Subject → insert body with
  `document.execCommand('insertText')` at a collapsed-to-start selection.
  `fill()` WIPES the signature; `insertHTML` is blocked by Trusted Types.
- **Gmail SILENTLY DROPS saves under rapid scripted composing** (~56 of 77 lost on
  the first pass). Always wait for the compose header's "Draft saved" ack before
  Save & close, pace ≥1s, and VERIFY the drafts count grew after each chunk.
- Send = focus body + ⌘Enter; sending 10-batches with ~6.5 min gaps tripped no bot
  protection.
- `getByRole('link', {name:/KaraokeHunt/})` matches the SIDEBAR LABEL "KaraokeHunt"
  — scope row locators to `tr.zA` or match on full subjects.
- Gmail search does NOT reliably index fresh drafts (to:, body, or subject) —
  operate on the #drafts folder rows, not search.
- Bulk automation: `browser_run_code_unsafe` with a script file under
  `.playwright-mcp/kh-scripts/` (allowed root). Reusable scripts there:
  `kh_send10.js` (send 10 oldest KH drafts), redo/draft chunk templates.
- Postmark 168h is the login-link expiry_hours MAX (422 above).
- Email content conventions (LOCKED): hyphens never em-dashes; original request
  date in opening ("a while back (April 1st 2025)…", softened if <2 months);
  NO bilingual bodies (PS strip + localized page instead); "Cheers,\nAndrew".

## 6. Where everything lives

Local PII dir (NEVER commit): `old-or-other/karaokehunt-users-export-2026-09-09/outreach_out/`
- `drafted_emails_2026-09-24.json` — full text of all 77 sent emails (with types).
- `minted_links_2026-09-24.txt` — email → job_id → one-click URL for the 35 parked.
- `batch_state.json` / `phasec_state.json` / `actions_v3.json` /
  `language_inference.json` / `review_packet_bc.md` — as before (phase-c handoff §2).

Docs: phase-c handoff (history), this doc (continuation), workspace session records
`docs/sessions/2026-Q3/2026-09-24-karaokehunt-segment-a-complete.md` (+ 09-19/09-22 ones).
