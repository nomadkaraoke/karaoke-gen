# Requests Voting Board (`requests.nomadkaraoke.com`)

**Status:** Phase 1 **LIVE in production** (karaoke-gen v0.219.0, PR #975, 2026-09-02).
Phase 2 (the daily automation) is **built and merged, deployed dark** behind a kill-switch
(`COMMUNITY_DAILY_PICK_ENABLED`, default off) — flip it on to go live. Trending-agent fallback
source is the one deferred piece. Design: `docs/archive/2026-09-03-requests-board-phase2-plan.md`.

This doc is the single self-contained explanation of what the requests system is, what users
experience, and how it works. Point marketing / YouTube-description work here.

---

## The idea (the vision)

**Give away one free, fully-made karaoke video every day — to whichever song the public most
wants — and use that to turn curious viewers into Nomad Karaoke users.**

A public, Hacker-News-style board where anyone can:
- **request** a song (just artist + title), and
- **vote** for the songs they want made.

Every day, the most-voted song is generated for free and published to YouTube. It's a low-friction,
zero-cost thing for a viewer to do (voting on a song you want costs nothing), which makes it a great
top-of-funnel call-to-action: far more people will click "vote for a free karaoke track" than "buy
credits". Once someone has an account and an email on file, we can gently convert a share of them
into paying [gen.nomadkaraoke.com](https://gen.nomadkaraoke.com) customers over time.

## Where it lives

- **Public URL to share / link:** **https://requests.nomadkaraoke.com**
  (redirects to the board; this is the clean vanity link to put in YouTube descriptions, socials, etc.)
- Also directly reachable at `https://gen.nomadkaraoke.com/en/requests` (any of the 33 locales:
  `/es/requests`, `/de/requests`, …).

## What a user experiences (Phase 1 — live today)

1. **Open the board.** Anyone can view it without signing in — they see the ranked list of
   requested songs and how many votes each has.
2. **Sign in with just an email.** No password. They enter their email, get a magic-link, click it,
   and they're in. (Signing in here is *identity only* — it does **not** spend a karaoke credit.)
3. **Request a song.** They type an artist and title. We auto-correct/canonicalize it with the same
   AI the main generator uses (e.g. "beatles" → "The Beatles", "Maduk" → "Maduk feat. Kye Sones"),
   show them "we tidied that to …", and add it to the board. If someone already requested that same
   song, their submission just up-votes the existing entry instead of creating a duplicate.
4. **Vote — once per day.** Each person gets **one vote per calendar day** (up or down, on a single
   song). They can move it to a different song later that day, or come back tomorrow for a fresh
   vote. This keeps the signal strong and hard to game. Submitting a song counts as that day's vote
   for it.
5. **See the ranking.** The board is sorted by net votes — the top song is what's next in line.
6. **"Don't want to wait? Make it yourself now →"** A prominent call-to-action lets an impatient
   user jump straight into the full generator and make their video immediately — and *that* path
   grants them the standard free welcome credit (once per account, the normal way).

### Phase 2 — the automation (built, deployed dark)
The daily pick-and-generate, the free-credit grant to the winning requester, the 24-hour
"if you don't finish it, it passes to the next voter" hand-off, and the "your song is live on
YouTube!" fan-out to every voter are all **built and merged**, gated behind the
`COMMUNITY_DAILY_PICK_ENABLED` kill-switch (default **off**). While off, the daily Scheduler still
runs but only **shadow-logs** the pick it *would* make (also reachable manually with
`?dry_run=true`) — so behavior can be verified before going live. Flip the env var on to start
donating one free track per day. The only outstanding piece is the **trending-agent fallback
source** (auto-submit a candidate when the board is empty), deliberately deferred to a follow-up.

**How the automation works:**
- **Daily picker** (`community_daily_pick.py`, Scheduler noon US Eastern) claims the UTC day with a
  create-only lock (`daily_community_pick/{YYYY-MM-DD}` → **one free track/day, total**), picks the
  top open request with net votes ≥ 0 (source-agnostic; skips community-rejected), grants the
  requester a free credit, and submits the job **as that user** (so it's owned by them and the credit
  is consumed) via the same search → auto-select → download path the web flow uses.
- **24h hand-off** (`community_handoff.py`, hourly) reassigns a track to the next up-voter if the
  owner hasn't completed their review within 24h — up to 5 voters, then it parks the track
  (`stalled`). Community jobs are excluded from the normal stale-review auto-cancel.
- **Publish fan-out** hooks the YouTube upload queue: when a community pick goes live it's marked
  `published` and every up-voter (except the owner, already emailed) gets a "track you voted for is
  live" email.
- Every step is idempotent (durable per-day lock phase + per-request guard flags) so a retried
  Scheduler delivery or a mid-run crash can't double-grant credits or make two tracks in a day.

## How it works (technical, brief)

- **Frontend:** a route in the gen Next.js app (`frontend/app/[locale]/requests/`), fully translated
  to 33 locales. `requests.nomadkaraoke.com` is a Cloudflare redirect to it (see
  `docs/runbooks/requests-subdomain-cloudflare.md`).
- **Auth:** reuses gen's passwordless magic-link system. Board sign-in sends
  `purpose="requests_board"`, which creates an identity **without** granting a welcome credit and
  with a higher per-IP signup cap (so a whole venue on shared WiFi can sign in).
- **Auto-correct:** reuses `match_judge` (Vertex Gemini) — the same artist/title canonicalizer the
  job-submission flow uses.
- **Storage:** two Firestore collections — `song_requests` (one doc per song, with a denormalized
  `vote_count`) and `song_request_votes` (one doc per person per day, id `{email}__{YYYY-MM-DD}`,
  which is what structurally enforces one-vote-per-day).
- **API:** `GET /api/requests-board/requests` (public), `POST /api/requests-board/requests` (submit),
  `POST /api/requests-board/requests/{id}/vote`, `GET /api/requests-board/me`, and
  `POST /api/users/claim-welcome-credit` (the convert-to-gen credit). Full reference in `docs/API.md`.

## Guidance for marketing / YouTube descriptions

- **Link to use:** `https://requests.nomadkaraoke.com`
- **The hook:** it's *free* and *low-effort* — "Vote for the next free karaoke track" / "Request the
  song you want made — we make the most-voted one free every day." Emphasise there's nothing to buy
  and no password.
- **Honest framing while Phase 2 is pending:** the board is live and collecting requests/votes now;
  the fully-automatic "one free track every day" cadence is rolling out. It's safe to say "request &
  vote for the songs you want us to make" today; be a little careful about promising a *guaranteed
  daily* free track until Phase 2 ships (until then, picks are made as we go). If in doubt, frame it
  as "vote for what we make next."
- **Conversion:** the board itself nudges impatient users into the paid generator ("Make it yourself
  now"), so linking the board also feeds gen signups.

## Related docs
- Product/build plan: `docs/archive/2026-09-02-requests-voting-board-plan.md`
- **Phase 2 handoff (start here to build the automation):**
  `docs/archive/2026-09-03-requests-voting-board-phase2-handoff.md`
- Subdomain wiring runbook: `docs/runbooks/requests-subdomain-cloudflare.md`
- API reference: `docs/API.md` (§ Requests Voting Board)
