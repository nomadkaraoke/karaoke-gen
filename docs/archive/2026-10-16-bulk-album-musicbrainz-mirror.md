# Bulk album mode: our MusicBrainz mirror only (2026-10-16)

## Incident
`/app` → Bulk → By album → "Britney Spears" showed **"Lookup failed. Please try again."**
`GET /api/bulk/album/artists` returned 502: all 3 attempts to `https://musicbrainz.org/ws/2/artist`
died with `httpx.ConnectError('')` from Cloud Run, while the same request from a home IP returned 200
in 0.5 s, and `data.metabrainz.org` (dump downloads) still worked from GCP. The flow last worked on
2026-09-29. Most likely MusicBrainz is refusing our shared GCP egress range (they block IPs that hit
the API hard, and cloud ranges get caught by scraper blocks).

## Decision
Andrew: *"we should only be querying our own infra... not just as a fallback, i don't want to be
abusing musicbrainz!"* So the live API is gone entirely, with no fallback.
`backend/services/musicbrainz_service.py` now reads only the BigQuery mirror that karaoke-decide's
weekly `mb-refresh` job builds from the official dumps:

| Lookup | Table (cluster key used) | Billed / latency |
|---|---|---|
| Artist search | `mb_artists_normalized` (exact / "the "+q → prefix → substring, then Spotify popularity) | ~0.3 GB / ~0.6 s |
| Albums | `mb_artist_credit_artists` (artist_mbid) → `mb_release_groups` | ~0.4 GB / ~1 s |
| Editions | `mb_releases` (release_group_mbid) | ~0.03 GB / ~0.7 s |
| Tracklist | `mb_release_tracks` (release_group_mbid, release_mbid), **new in karaoke-decide v0.14.0** | MBs / ~0.7 s |

- Queries go through the BigQuery REST `jobs.query` endpoint (httpx + ADC), so there's no new
  dependency. They're parameterized, capped at `maximumBytesBilled` = 2 GiB, and cached in memory
  for 10 min.
- `mb_tracks` is clustered by `recording_mbid` first, so a `release_mbid` filter on it scans
  ~9 GB (≈$0.06 per album). That's why decide gained `mb_release_tracks`.
- Edition switches now send the album's `release_group_mbid` so both reads prune. Without it (an
  older frontend), the backend first looks the release group up, which scans ~0.2 GB.
- Mirror editions carry every release-event country (`countries`). Canonical-pressing selection
  ranks an edition by its best-ranked country.
- IAM: `karaoke-backend` SA gets project-level `bigquery.dataViewer` + `bigquery.jobUser`
  (`infrastructure/modules/iam/backend_sa.py`). decide's Pulumi owns the `karaoke_decide` dataset
  ACL, so a dataset-scoped grant from gen would fight it.
- Freshness: the data is at most a week behind MusicBrainz, which is fine for album tracklists.

## Rollout order
1. Merge the karaoke-decide PR (`mb_release_tracks` model). Then build the table, either at the next
   Sunday `mb-refresh` run or once by hand (13 GB scan, ≈$0.08):
   `CREATE OR REPLACE TABLE nomadkaraoke.karaoke_decide.mb_release_tracks CLUSTER BY release_group_mbid, release_mbid AS SELECT t.* EXCEPT(track_mbid), r.disambiguation AS recording_disambiguation FROM nomadkaraoke.karaoke_decide.mb_tracks t LEFT JOIN nomadkaraoke.karaoke_decide.mb_recordings r USING (recording_mbid)`
2. Apply the gen Pulumi change (IAM), then merge/deploy gen.
