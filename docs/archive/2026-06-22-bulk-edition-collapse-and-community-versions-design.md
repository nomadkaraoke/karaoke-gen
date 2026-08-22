# Bulk Mode — Edition Collapse + Community-Versions Redesign (Design)

**Date:** 2026-06-22
**Branch:** `feat/sess-20260622-0041-bulk-release-selection`
**Scope:** karaoke-gen Bulk Mode "By album" + "By text" flows, against **live MusicBrainz** (no
mirror dependency).

## Context

Bulk Mode lets a user submit up to 100 karaoke jobs at once. The "By album" flow searches
MusicBrainz, picks a release, and shows a tracklist with checkboxes + community-version badges.
Two UX problems, both visible in production:

1. **The edition dropdown is noise.** For ABBA *Arrival* it showed ~17 editions, most of which were
   the *same* 1976 10-track album pressed in different countries (US/AU/SE/NL/DE…). The selection's
   only job is to produce a list of **track names** for submission — the country of pressing is
   irrelevant to that.
2. **The community-version badge is wide and inert.** It renders
   `Community version: SNDL Karaoke, Nomad Karaoke, Reekies Karaoke` inline, which grows
   horizontally with the number of brands and is not clickable — the user can't preview a version to
   decide whether it's good enough or whether to remake it.

This is the **near-term, live-MB** improvement. The larger mirror-backed upgrade (exact
tracklist-signature grouping + popularity ranking) is captured separately in
`../../../docs/archive/2026-06-18-musicbrainz-mirror-and-album-ux-handoff.md` (Part 3a, Option 2)
and is **out of scope here**.

## Goals

- Collapse the edition list into a handful of distinct **tracklist variants**, not country pressings.
- Make the common case a single confident default; hide the choice behind "Change".
- Show **track numbers**.
- Replace the community-version badge with a **vertical, clickable** list of versions, each linking
  to its YouTube video so the user can preview it.

## Non-goals

- The MusicBrainz mirror (decide) and anything that depends on it (exact signature grouping,
  popularity-based auto-select). Tracked in the handoff doc.
- Changing how jobs are submitted, credited, or processed downstream.
- Fetching per-edition tracklists to compare them (would trip MB's egress rate limiter — explicitly
  avoided; see Option 1 rationale).

---

## Feature 1 — Edition selection: collapse to tracklist variants (A + C)

### Model
- **A — collapse to variants.** Group the release-group's official editions into distinct tracklist
  variants. Present only meaningfully-different tracklists. Country is no longer the headline.
- **C — single default, choice hidden.** Show one line; reveal the variant list only on *Change*; if
  there's a single variant, no Change affordance at all.

### Grouping key — Option 1 (track count)
Group official editions by `track_count`. Within a single release-group, same track count ≈ same
songs (reissues *add* tracks ⇒ a different count), so this is ~95% correct with **zero extra MB API
calls** (`_list_editions()` already returns `track_count` per edition). Known, accepted gap: a rare
reissue that swaps one track 1-for-1 (same count, different song) would merge incorrectly — the user
still sees the full tracklist before submitting. Tracklists stay **lazy-fetched** only for the
selected variant, exactly as today.

> Rationale for not fetching every edition's tracklist: MusicBrainz rate-limits hard from Cloud Run
> egress (we've hit 503s and added retry/backoff). Comparing 17 editions = 17 extra calls per album,
> in a flow where users look up many albums. The exact-signature approach waits for the mirror.

### Backend (`backend/services/musicbrainz_service.py`, `backend/api/routes/bulk.py`)

`get_album_tracklist(release_group_mbid)` already runs `_list_editions()` →
`pick_canonical_edition()` → `get_release_tracklist(canonical)`. Add a variant-building step after
`_list_editions()`:

1. Filter to `status == "Official"` (mirrors `pick_canonical_edition`).
2. Group by `track_count`.
3. For each group, choose a **representative release** using the existing canonical heuristic scoped
   to the group: earliest date, then locale-preferred country (see Locale), then a stable fallback.
4. Compute each group's earliest year (for display) and `pressing_count` (how many editions
   collapsed in).
5. **Label** variants:
   - The group with the overall-earliest date → `label = "Original"`.
   - Other groups → labeled by track-count delta vs the Original: more tracks ⇒ `"+N tracks"`
     (rendered as e.g. "Reissue · +2 tracks"); fewer ⇒ `"-N tracks"`. Year disambiguates.
6. Sort variants: Original first, then by year ascending.

The **default** tracklist remains the canonical release = the Original variant's representative
(no behavior change to the default load). Switching variants on the frontend reuses the **existing**
`GET /album/tracklist?release_mbid=<representative_release_mbid>` path — no new endpoint.

New response field on `BulkTracklistResponse`:

```python
class BulkEditionVariant(BaseModel):
    representative_release_mbid: str
    label: str            # "Original" | "Reissue" | derived
    track_count: int
    year: str = ""        # earliest year in the group, e.g. "1976"
    pressing_count: int = 0
    delta_vs_original: int = 0   # track_count - original.track_count

class BulkTracklistResponse(BaseModel):
    ...
    variants: list[BulkEditionVariant] = []
    selected_variant_mbid: Optional[str] = None  # which variant the returned tracklist belongs to
```

`editions[]` may stay for back-compat but is no longer the primary UI input. `selected_variant_mbid`
lets the frontend highlight the active variant after a switch.

### Locale
The frontend passes the active locale (`useLocale()` from next-intl) to `/album/tracklist`
(e.g. `?locale=en`). Backend maps locale → ordered country-preference list used in the
representative pick, falling back to today's `[XW, XE, US, GB]`:

| locale prefix | preferred countries |
|---|---|
| en | GB, US, AU, CA, IE, NZ |
| ja | JP |
| de | DE, AT, CH |
| ko | KR |
| es | ES, MX, AR |
| fr | FR, CA, BE |
| (other / unknown) | XW, XE, US, GB |

Under Option 1 this mostly affects *which pressing backs a variant*, not which variant is default
(Original/earliest is always default). It wires the hook the mirror era will lean on.

### Frontend (`frontend/components/job/bulk/BulkAlbumMode.tsx`, `lib/api.ts`, `bulk/types.ts`)
- Replace the raw editions `<Select>` with the **C** pattern:
  - One summary line: *"Using the {label} {year} release · {N} tracks"* + a **Change** button.
  - **Change** opens the variant list (reuse `<Select>` / a small popover) showing each variant's
    label, year, track count, and (subtly) pressing_count. Selecting one calls the existing
    `release_mbid` fetch.
  - If `variants.length <= 1`, render the summary line **without** a Change affordance.
- **Track numbers:** prefix each track row with `position` (e.g. `1.`). Data already present on
  `BulkTrack.position`; currently unrendered. Hide if `position` is null.

---

## Feature 2 — Community versions: vertical, clickable list

### Data — stop discarding `youtube_url`
`_parse_single_track()` in `karaokenerds_service.py` already captures
`{brand_name, brand_code, youtube_url, is_community}`. `check_community_versions_batch()` flattens it
to `brands: [str]`, dropping the URL. Enrich it to also return per-version objects:

```python
# per result, in addition to existing available/brands/brand_count:
"versions": [{"brand": brand_name, "url": youtube_url}, ...]
```

Dedup by `brand` (first community `youtube_url` per brand). `brands`/`brand_count` retained for
back-compat (existing tests, any other consumers).

### Backend plumbing (`backend/api/routes/bulk.py`)
- `BulkTrack`: add `versions: list[CommunityVersion] = []` where
  `CommunityVersion = {brand: str, url: str}`.
- `AvailabilityResult`: add `versions: list[CommunityVersion] = []`.
- `/album/tracklist`: set `t["versions"] = a["versions"]` in the enrichment loop.
- `/availability` (text mode): pass `versions` through.

### Frontend (`frontend/components/job/bulk/AvailabilityBadge.tsx` → versions list)
Replace the single wide badge with a vertical block when `available && versions.length`:

```
Existing versions:
 ▶ SNDL Karaoke         (anchor → version.url, target=_blank rel="noopener noreferrer")
 ▶ Nomad Karaoke
 ▶ Reekies Karaoke
```

- lucide `Youtube` icon per row.
- **Show all versions, always** (no cap/expander) — multiple community versions of one song is rare;
  vertical layout already solves the original width problem.
- Each row is a link opening the YouTube video in a new tab; clicking it must **not** toggle the
  track checkbox.
- Fallback: if `available` but `versions` is empty (shouldn't happen — a community track always has a
  URL), render the existing plain "Community version exists" text.
- Shared by album and text modes (both render this component).

Track-selection default logic is unchanged: tracks with `is_extra` or `available` start **unticked**
(`shouldSelectTrack` in `types.ts`).

---

## i18n
Per workspace policy, any `messages/en.json` change ships with all 33 locales.
- New/changed keys (bulk namespace): `existingVersions` ("Existing versions:"),
  `usingRelease` ("Using the {label} {year} release · {count} tracks"), `changeEdition` ("Change"),
  `variantOriginal` ("Original"), `variantReissue` ("Reissue"), `variantTrackDelta` ("+{n} tracks").
- Remove now-unused `communityExistsWithBrands` only if no other consumer references it.
- Run `python scripts/translate.py --messages-dir ./messages --target all` before the PR.

## Testing
- **Backend unit (`musicbrainz_service`):** variant grouping — multiple same-track-count editions
  collapse to one variant; differing counts → separate variants; Original = earliest; representative
  pick honors locale country preference then falls back; single-edition album → one variant.
- **Backend unit (`karaokenerds_service`):** `check_community_versions_batch` returns `versions` with
  brand+url, deduped by brand; still returns `brands`/`brand_count`; failed lookup → empty, never
  raises. Parser test: a `<li class="track">` with a YouTube href yields `youtube_url`.
- **Backend API (`bulk.py`):** `/album/tracklist` response includes `variants` and per-track
  `versions`; `/availability` includes `versions`. Add a regression test that `variants` and the new
  fields survive the Pydantic round-trip.
- **Frontend:** AvailabilityBadge renders one anchor per version with correct href + `target=_blank`;
  clicking a version link does not toggle the checkbox; edition summary line + Change toggle; track
  numbers rendered from `position`; single-variant hides Change.

## Risk / rollback
- Pure additive backend fields + frontend rendering swap; no job-submission or money-path changes.
- Option 1's merge edge case (1-for-1 track swap) is visible to the user before submit — acceptable.
- If MB shape surprises us, variant building degrades to "one variant = canonical" (current behavior).

## Files touched (anticipated)
- `backend/services/musicbrainz_service.py` — variant grouping, locale country pref.
- `backend/services/karaokenerds_service.py` — `versions` in batch result.
- `backend/api/routes/bulk.py` — models (`BulkEditionVariant`, `CommunityVersion`, `versions`
  fields), tracklist/availability plumbing, accept `locale`.
- `frontend/lib/api.ts` — types for variants + versions.
- `frontend/components/job/bulk/types.ts` — `BulkTrack.versions`, variant types.
- `frontend/components/job/bulk/BulkAlbumMode.tsx` — C-pattern edition picker, track numbers, pass locale.
- `frontend/components/job/bulk/AvailabilityBadge.tsx` — vertical clickable versions list.
- `frontend/messages/en.json` (+ 32 locales via translate.py).
- Tests alongside each.
