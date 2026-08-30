# PyPI storage-cap prune runbook

## The problem

PyPI caps **total project size at 10 GB**. `karaoke-gen` publishes a universal
`py3-none-any` wheel on every merge to `main` (auto-versioned in CI). Since the
i18n initiative the wheel bundles a Next.js static export pre-rendered into 33
locales (the CLI local lyrics-review UI serves it at runtime), so each release is
**~62 MiB**. At ~50 releases/month that's **~3 GB/month**, so the project refills
the cap every few months and the publish step fails:

```
HTTP Error 400: Project size too large. Limit for project 'karaoke-gen'
total size is 10 GB.
```

Only the public `pip install` publish step fails — the Cloud Run backend and the
GCE encoding worker (which pulls the wheel from **GCS**, not PyPI) are unaffected.

## Why we can't just automate deletion

PyPI has **no deletion API**. Upload tokens and Trusted Publishing are
upload-only, and PyPI mandates 2FA, so deletion is only possible from a
logged-in browser session — which can't be safely or durably stored in CI. So we
automate the *decision and the reminder*, and keep the *deletion* a ~1-minute
human step.

## Retention policy

Keep every release from the **last 60 days**, plus the **newest release of each
older calendar month**, plus the **latest release overall**. Delete the rest.
Run recurringly, this holds steady-state around 6–7 GB — permanently under the
cap. Deletion is irreversible and a deleted version/filename can never be
re-uploaded; that's acceptable here (nothing builds from source; PyPI is only the
public `pip install` channel, whose users want recent versions).

## How it runs

- **`.github/workflows/pypi-prune-reminder.yml`** — monthly cron (1st, 09:00
  UTC) + manual `workflow_dispatch`. Computes the plan and, when there's anything
  to prune, opens or updates a single tracking issue titled
  **"🧹 PyPI storage prune due: karaoke-gen"** with the plan + a paste-in
  deletion snippet. No secrets.
- **`scripts/prune_pypi_releases.py`** — the planner. Reads the public JSON API,
  applies the policy, and renders a table / JSON / Markdown / browser-console JS.
  It never deletes anything itself.

## How to prune (≈1 minute)

1. Open the tracking issue (or run the script locally, below) to get the snippet.
2. Go to **<https://pypi.org/manage/project/karaoke-gen/releases/>** and log in.
3. Open browser devtools → **Console**.
4. Paste the snippet and press Enter. It reads the page's CSRF token and POSTs a
   whole-version delete for each planned version, ~0.5s apart, logging progress.
5. Reload to confirm. If a publish was blocked, re-run it: push an empty commit /
   re-run the failed CI `Deploy - Publish to PyPI` job, or `poetry publish`
   locally.

> The aggregate `pypi.org/pypi/karaoke-gen/json` is Fastly-cached and lags a few
> minutes after deletes — trust the logged-in manage page for live state.

## Running the planner locally

```bash
# Dry-run table (what would be deleted, and the resulting size)
python scripts/prune_pypi_releases.py

# Just the paste-in deletion snippet
python scripts/prune_pypi_releases.py --format console-js

# Machine-readable / the Markdown the workflow posts
python scripts/prune_pypi_releases.py --format json
python scripts/prune_pypi_releases.py --format markdown

# Tune the window (default 60 days) or target another project
python scripts/prune_pypi_releases.py --keep-days 90 --project karaoke-gen
```

Stdlib only — no dependencies to install.

## Related

- One-off escape hatch: request a PyPI storage-limit increase via the
  `limit-request-project` template on
  [`pypi/support`](https://github.com/pypi/support) (we filed
  [pypi/support#12050](https://github.com/pypi/support/issues/12050) for 50 GB).
  A larger cap just buys time; the recurring prune is the durable fix.
- History of the first time this bit us: `docs/LESSONS-LEARNED.md`.
