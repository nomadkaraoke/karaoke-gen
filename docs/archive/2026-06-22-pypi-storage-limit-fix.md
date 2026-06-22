# PyPI 10 GB Storage-Limit Fix (karaoke-gen)

**Date:** 2026-06-22
**Repo:** `karaoke-gen`
**Status:** ✅ Resolved & verified in production
**Shipped in:** PR #851 (v0.187.5) + a one-time PyPI release deletion
**Related earlier prompt:** `nomadkaraoke/docs/archive/2026-06-18-pypi-storage-limit-fix-prompt.md` (the task spec this session executed; now superseded by this doc)

---

## TL;DR

The PyPI project `karaoke-gen` hit PyPI's **10 GB total-size cap**, so every
`poetry publish` on merge to `main` failed with HTTP 400. We fixed it two ways:

1. **One-time deletion** of 42 old bloated releases (`0.172.1`–`0.182.2`),
   freeing ~4.35 GB → project down to **~6.36 GB** (3.64 GB headroom).
2. **Wheel-only publish** going forward (`poetry build --format wheel`), which
   halves per-release cost (**104 MB → 54 MB**) by no longer publishing the
   redundant source distribution.

Both were needed: deletion unblocks *now*; wheel-only slows *recurrence*.

---

## Symptom

`Deploy - Publish to PyPI` job in `.github/workflows/ci.yml` failed on every
push to `main`:

```
HTTP Error 400: Bad Request
Project size too large. Limit project 'karaoke-gen' total size 10 GB.
See https://docs.pypi.org/project-management/storage-limits
```

The **build** succeeded; only the **upload** was rejected (a project-wide cap,
not a per-file or version-conflict error). Backend (Cloud Run), frontend
(Cloudflare Pages), and the GCE encoding worker were all unaffected — only the
public `pip install karaoke-gen` / `karaoke-gen-remote` path was blocked.

---

## Diagnosis (measured, not assumed)

Pulled the PyPI JSON API and summed file sizes per release:

- **10.711 GB across 444 releases** (over the 10 GB cap).
- Size distribution was bimodal: **387 small releases** (~11 MB median, all
  `≤0.171.x`) and **57 fat releases** (~104 MB each = 53.7 MB wheel + 50.2 MB
  sdist) — the fat ones alone were >5.8 GB.
- Chronological inflection: the wheel jumped **18 MB → 102 MB on 2026-04-23 at
  v0.172.1** and stayed fat for every release after.

### Root cause: the i18n initiative

The wheel/sdist bundle the Next.js static export
(`karaoke_gen/nextjs_frontend/out/**`). When i18n shipped (Apr 2026),
`next build` began pre-rendering the **entire app into 33 locale directories**
(`hi/`, `th/`, `el/`, `uk/`, `ru/`, `ar/`, `ja/`, …), each ~3.4 MB of HTML.
That's ~112 MB uncompressed — a 33× duplication of the page HTML. The shared JS
(`out/_next`) is only 4.5 MB; the locale HTML is the bloat. Bundling this in
**both** the wheel and the sdist, multiplied across ~57 releases, blew past the cap.

It was *not* "too many patch versions" — the 387 pre-i18n releases together were
under half the total.

### Why the frontend can't simply be dropped from the wheel

The **CLI local-review server reads `out/` at runtime**
(`karaoke_gen/lyrics_transcriber/review/server.py` → `_mount_frontend` /
`_discover_locales`). It serves locale-prefixed routes (`/{locale}/app/jobs/local/review`)
because the `LocaleRedirect` client component bounces the browser to
`/{locale}/...` by browser preference, and it 404s on any locale not present on
disk. So the wheel must keep the full `out/` tree. (The sdist, however, never
needs it — see the fix.)

### Blast radius (why deletion was safe)

- Nothing in the workspace pins `karaoke-gen==x.y.z` (checked all
  `*.txt/*.toml/*.cfg/Dockerfile/*.sh`).
- The GCE encoding worker pulls the wheel from **GCS**
  (`gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-current.whl`), not PyPI.
- Backend/frontend deploys are independent of PyPI.
- So PyPI exists only for public `pip install`. Deleting old patch versions has
  essentially zero internal blast radius.

---

## Decisions (Andrew's calls)

1. **Packaging:** chose wheel-only publish over trying to trim only the frontend
   out of the sdist (see "Gotcha" below — the trim wasn't a config tweak).
2. **Deletion:** approved a **surgical** delete of the *older* fat releases,
   keeping the 15 most recent fat releases and all small historical ones —
   rather than a blunt "keep latest N" that would drop old historical versions.

---

## What we did

### Part 1 — Delete 42 old fat releases (irreversible; web UI)

**Kept:** the 15 most recent fat releases (`0.183.0`–`0.187.2`) + all 387 small
releases (`≤0.171.x`).
**Deleted:** the 42 fat releases `0.172.1`–`0.182.2`.
**Result:** 10.711 GB → ~6.36 GB.

**How (there is no PyPI deletion API — tokens are upload-only):** drive the
logged-in PyPI web UI via browser automation. For each version:

- Whole-release delete is a POST to
  `/manage/project/karaoke-gen/release/<version>/` from the modal
  `#delete_version-modal`, with two fields: a session-wide `csrf_token` (hidden)
  and `confirm_delete_version` set to the **version string** (not the project name).
- We GET each release's manage page, scrape its `csrf_token`, verify the form
  action matches the exact version, then POST with `credentials: 'include'`,
  ~400 ms apart. A hardcoded keep-list guard ensured the 15 kept versions were
  never touched. Success = the POST redirects to `/releases/`.

**Verification gotcha:** the aggregate `pypi.org/pypi/karaoke-gen/json` endpoint
is Fastly-cached and lagged for minutes (still showed 444 releases after
deletion). Verify instead via the **logged-in manage page** (live: showed
exactly 402 releases, all deleted versions absent, all kept versions present) or
the **per-version** endpoint `pypi.org/pypi/karaoke-gen/<v>/json` (404s
immediately on delete).

### Part 2 — Wheel-only publish (PR #851, v0.187.5)

The sdist is dead weight: we ship a universal `py3-none-any` wheel, pip always
installs from it, and nothing builds karaoke-gen from source. So we stopped
building/publishing the sdist:

- `poetry build --format wheel` in **both** the `Package - Build & Install (Test)`
  job and the publish-feeding `Package - Build` job in `.github/workflows/ci.yml`.
- Removed `dist/*.tar.gz` from the GitHub release attachments (it no longer exists).
- Bumped `0.187.4 → 0.187.5`.

The wheel is **unchanged** — still bundles the full `out/` the CLI review server
needs (verified: built wheel contained all 3575 `out/` entries + the 3 console
entry points). Per-release PyPI cost: **104 MB → 54 MB**.

#### Gotcha: why we did NOT just trim `out/` from the sdist

The "obvious" fix — keep a small sdist without the frontend — does **not** work
with a config tweak, because:

- `out/` is **git-tracked** under the `karaoke_gen` package, so Poetry pulls it
  into the sdist as package data.
- **Poetry's `exclude` does not remove VCS-tracked files**, even when scoped with
  `format = "sdist"`. We tried `exclude = [{ path = ".../out/**/*", format = "sdist" }]`
  and rebuilt — the sdist still contained all 3575 `out/` entries.
- `include` takes precedence over `exclude`, so a wheel-scoped `include` for the
  same path also kept it force-included.

Trimming the sdist *properly* would have required **untracking `out/` from git**
(`git rm --cached`, gitignore it), adding a wheel-only `include`, and adding a
frontend build+copy step (`make build-frontend`: `npm run build` →
`cp -r frontend/out/* karaoke_gen/nextjs_frontend/out/`) to the `package-build`
CI job — which currently has no frontend build and relies on the committed
`out/`. That's a bigger, riskier change (CI builds the frontend in the publish
path, large git diff, local `poetry build` would need a frontend build first).
Wheel-only achieves the same storage win with one-line changes and saves more.

---

## Verification (production)

- `Deploy - Publish to PyPI` job **succeeded** on the post-merge run (no 400).
- **0.187.5 is live on PyPI as wheel-only** — one file: the 53.7 MB
  `karaoke_gen-0.187.5-py3-none-any.whl`, no `.tar.gz`.
- Project size ~6.36 GB (3.64 GB headroom).
- Encoding worker VM was auto-stopped (idle, normal cost-saving). It is
  unaffected — the wheel content and the GCS wheel-upload step are unchanged; it
  pulls `karaoke_gen-current.whl` from GCS as usual on its next boot.

---

## If this recurs (runbook)

**Headroom math:** at 54 MB/release with ~3.64 GB headroom, that's ~65 more
releases before the cap bites again — months at current cadence.

When the publish starts 400ing again:

1. **Measure:** sum per-release sizes from `pypi.org/pypi/karaoke-gen/json`
   (use a file + `python3`, not piped curl — output filters can mangle it).
   Identify the fat band and a safe keep-set (latest N + anything still referenced).
2. **Delete surgically** via the browser web-UI technique in Part 1 (no API).
   Verify via the logged-in manage page / per-version JSON, NOT the cached
   aggregate JSON.
3. **Confirm** the wheel itself hasn't re-bloated. If the frontend grew, the next
   lever (more involved) is to stop committing `out/` and host/download it instead
   of bundling — see the "Gotcha" above for the shape of that change.
4. PyPI also accepts storage-limit-increase requests (free, slow, days–weeks) as
   a fallback if deletion isn't enough.

**Do not** rely on `exclude` to slim the sdist while `out/` is git-tracked — it
won't work.
