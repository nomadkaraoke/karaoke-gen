# YouTube Descriptions: new template + bulk rewrite + automated drain

**Date:** 2026-09-03 · **Status:** built, tested locally; awaiting deploy (Pulumi + merge)

## Goal

The 1.6k videos on youtube.com/@nomadkaraoke had inconsistent descriptions: old
ones linked a dead $10 Fiverr gig; newer ones led with "AI-powered" (bad rep) and
linked nowhere useful. Unify them all to one consistent, conversion-focused
template, make new uploads use it too, and make the whole thing reusable for any
future template change.

## The template

Single source of truth: `Settings.default_youtube_description` (backend/config.py),
rendered by `backend/services/youtube_description.py::render_youtube_description`
with per-video placeholders `{title} {artist} {artist_hashtag} {brand_code}`.

Key decisions (Andrew): removed all "AI" framing; leads with the craft; CTA to the
requests board (free, low-friction top-of-funnel) then the referral link
`nomadkaraoke.com/r/youtube` (50% off credits for 30 days, tracked via the referral
system); community links → Nomad Discord + decide.nomadkaraoke.com + KaraokeNerds;
dropped the "not monetized" claim; kept a fair-use copyright line; brand code
preserved. Requests-board wording uses honest Phase-1 framing (no guaranteed daily
auto-publish until Phase 2 ships).

## Live pipeline (new uploads)

Both upload sites now render via the shared module + enriched tags:
- `backend/workers/video_worker_orchestrator.py`
- `backend/workers/youtube_queue_processor.py`

`build_youtube_tags()` produces a richer tag set than the old `[karaoke, artist,
title]`.

## Bulk rewrite of the back catalogue

Shared logic in `backend/services/youtube_backfill.py` (classification, targeting,
render, update-body, channel enumeration, auth) is used by BOTH drivers below.

### Classification / safety
- Enumerate via the uploads playlist (cheap), fetch snippets, classify each.
- **Auto-eligible only at high confidence**: title ends in `(Karaoke)` AND parses
  to Artist/Title. The medium fallback (merely mentions "karaoke" + a hyphen)
  sweeps in demos/tutorials/appeals/lyrics-with-vocals, so those are excluded and
  surfaced for review.
- Curation lists ship in the package: `backend/data/youtube_backfill/skip_ids.txt`
  (never touch) and `include_ids.txt` (force-include, optional `id | Artist | Title`
  override for truncated titles).
- **A video is "pending" iff its LIVE description differs from the freshly rendered
  template** → idempotent and self-healing.

### First real analysis (2026-09-03)
1,631 videos returned: **1,610 high-confidence** targets + **4 force-included**
genuine tracks (Los Campesinos!, Arctic Monkeys — titles truncated by YouTube's
100-char limit, full titles restored via overrides; 2 Daniel O'Donnell medleys) =
**1,614 to rewrite**. **17 skipped** (3 lyrics-videos-with-vocals where "vocals
removed" would be false; 9 demos/tutorials/pitch/appeal; 5 unparseable non-karaoke).
Zero false positives. One video (`PmwRXIZoCpg`) updated live as a verified test.

### Driver 1 — CLI (manual): `scripts/youtube-descriptions/`
`analyze` / `apply` / `status`. Resumable local state in `.state/`. For analysis,
dry-runs, and one-off fixes. See that folder's README.

### Driver 2 — automated daily drain (production)
`backend/workers/youtube_description_backfill_worker.py`, triggered by Cloud
Scheduler → `POST /api/internal/youtube-backfill/run` (backend/api/routes/internal.py).
- **Progress state** in Firestore `youtube_backfill/state`, keyed by a template
  fingerprint. **Template changes → new cycle → whole channel re-drained**, no
  manual reset. This is the reuse mechanism.
- **Quota-coordinated**: each run computes a budget from
  `YouTubeQuotaService.get_quota_stats()` minus `youtube_backfill_quota_reserve`
  (default 3000 units reserved for uploads), capped by
  `youtube_backfill_daily_max_updates` (default 150). Records each update against
  the quota service so uploads see the consumption. Stops on API `quotaExceeded`.
- **Emails via Postmark** (`EmailService.send_email`) to
  `youtube_backfill_report_email` (default andrew@nomadkaraoke.com): a progress
  email each run, a completion email once the channel is fully drained.
- Enriches tags (`youtube_backfill_enrich_tags`, default true).
- Gated by `youtube_backfill_enabled` (default true).

Cloud Scheduler job added in `infrastructure/__main__.py`
(`youtube-description-backfill-daily`, 09:00 UTC, OIDC via backend SA).

At 150/day it finishes ~1,614 in ~11 days; tune via env or a quota bump.

## Config (backend/config.py)
`youtube_backfill_enabled`, `youtube_backfill_daily_max_updates`,
`youtube_backfill_quota_reserve`, `youtube_backfill_enrich_tags`,
`youtube_backfill_report_email`.

## Tests
- `backend/tests/test_youtube_description.py` — renderer + tags (15)
- `backend/tests/test_youtube_backfill.py` — classify/target/update-body/lists (19)
- `backend/tests/test_youtube_description_backfill_worker.py` — worker: full drain,
  budget cap, quota reserve, dry-run, completion/idempotency, template-change cycle,
  quotaExceeded stop (8)

## Deploy steps (Andrew — I'm GCP read-only)
1. Merge the PR → backend auto-deploys (worker + endpoint + config + data files).
2. `cd infrastructure && pulumi up` to create the Cloud Scheduler job (run locally
   before/after merge per repo convention).
3. Optional: trigger once manually to verify —
   `POST https://api.nomadkaraoke.com/api/internal/youtube-backfill/run?max_updates=5`
   with the admin token — watch for the progress email.
4. Let the daily job drain the channel; watch for the completion email.

## Future template change (the reuse path)
Edit `default_youtube_description` → deploy. Next scheduled run detects the new
fingerprint and re-drains all 1.6k videos automatically. No code changes needed.
