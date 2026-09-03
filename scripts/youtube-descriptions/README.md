# YouTube description tooling

Keep every video description on the Nomad Karaoke YouTube channel consistent with
the current template — the same one newly-published videos get.

There are **two** ways to drive this, both sharing one implementation
(`backend/services/youtube_backfill.py` for classify/target/render/update-body,
and `backend/services/youtube_description.py` for the template itself):

1. **Automated daily drain (production).** A scheduled backend worker
   (`backend/workers/youtube_description_backfill_worker.py`) triggered by Cloud
   Scheduler → `POST /api/internal/youtube-backfill/run`. It rewrites a
   quota-capped batch each day until the whole channel matches the template, then
   emails a completion note. **Whenever the template changes it automatically
   re-drains the whole channel** (a template-fingerprint in Firestore starts a
   fresh cycle) — this is the reusable, hands-off path. See the plan doc:
   `docs/archive/2026-09-03-youtube-descriptions-plan.md`.

2. **This CLI (manual / local one-offs).** For analysis, dry-runs, reviewing
   diffs, or updating specific videos by hand.

## CLI setup

Run from this directory using the karaoke-gen backend environment (has
`google-api-python-client` + Secret Manager access). Auth reuses the production
`youtube-oauth-credentials` secret (full read/write scope). If your ADC can't
read the secret, export it locally and pass `--credentials-file`:

```bash
cd scripts/youtube-descriptions
export GOOGLE_CLOUD_PROJECT=nomadkaraoke
gcloud auth login
gcloud secrets versions access latest --secret=youtube-oauth-credentials \
  --project=nomadkaraoke > creds.json      # gitignored
```

## CLI workflow

```bash
# 1) Read-only: enumerate + categorize the whole channel; write report + diffs.
python youtube_descriptions.py --credentials-file creds.json analyze
#    -> .state/report.md         (counts, review buckets)
#    -> .state/review-diffs.txt  (old -> new for every video that would change)
#    -> .state/analysis.json     (machine-readable, consumed by `apply`)

# 2) Review report.md + diffs. Curate the two lists in backend/data/youtube_backfill/:
#      skip_ids.txt     - never rewrite these
#      include_ids.txt  - force-rewrite these (optionally with a title override)

# 3) First live test on ONE video (bypasses resume-state; --update-tags enriches tags):
python youtube_descriptions.py --credentials-file creds.json apply --video-id VIDEOID --update-tags

# 4) Manual batch (resumable, quota-capped, shared 10k/day pool):
python youtube_descriptions.py --credentials-file creds.json apply --dry-run
python youtube_descriptions.py --credentials-file creds.json apply --daily-quota 6000 --update-tags

python youtube_descriptions.py status
```

## Curation lists (shared by CLI + worker)

Both live in the backend package so they ship in the Cloud Run image:

- `backend/data/youtube_backfill/skip_ids.txt` — one video ID per line (`#` comments).
- `backend/data/youtube_backfill/include_ids.txt` — bare `<id>`, or
  `<id> | <Artist> | <Title>` to override the parsed artist/title (used for
  genuine tracks whose `(Karaoke)` suffix was truncated by YouTube's title limit).

## Safety properties

- **`analyze` writes nothing to YouTube.**
- **High-confidence only + auto-skip.** Only titles ending in `(Karaoke)` are
  auto-eligible; demos/tutorials/lyrics-with-vocals land in review buckets and are
  never touched unless explicitly force-included.
- **Description-only by default.** `update()` re-sends the existing
  title/tags/category/language; only `--update-tags` refreshes tags.
- **Idempotent + resumable.** Only videos whose rendered description actually
  differs are updated.
- **Quota-capped.** `videos.update` = 50 units; the worker also reserves headroom
  for uploads via `YouTubeQuotaService`.

## Quota math

~1,600 videos × 50 units = ~80k units vs. 10,000/day (shared with uploads). The
worker paces itself; the CLI uses `--daily-quota`.

`.state/` and `creds.json` are gitignored.
