"""MusicBrainz lookup service for Bulk Mode album/release lookup.

Provides artist search, album (release-group) listing, canonical-release
selection, and tracklist retrieval with "extra track" detection (live /
remix / bonus / demo / acoustic / instrumental).

Reads ONLY our own MusicBrainz mirror in BigQuery (``karaoke_decide.mb_*``,
rebuilt weekly from the official dumps by karaoke-decide's ``mb-refresh`` job).
We never call the live MusicBrainz web service: it's a community-funded API and
our cloud egress has no business hammering it (see
``docs/archive/2026-10-16-bulk-album-musicbrainz-mirror.md``).

Queries go through the BigQuery REST ``jobs.query`` endpoint (httpx + ADC) so
the backend doesn't need the BigQuery client library. Every table read is
clustered on the column we filter by, so lookups bill MB, not GB:
- artists:  ``mb_artists_normalized`` (~0.3 GB — small, unclustered)
- albums:   ``mb_artist_credit_artists`` (artist_mbid) → ``mb_release_groups``
- editions: ``mb_releases`` (release_group_mbid)
- tracks:   ``mb_release_tracks`` (release_group_mbid, release_mbid)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

BQ_PROJECT = "nomadkaraoke"
MB_DATASET = f"{BQ_PROJECT}.karaoke_decide"
_BQ_QUERY_URL = f"https://bigquery.googleapis.com/bigquery/v2/projects/{BQ_PROJECT}/queries"
_BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery.readonly"]
_CACHE_TTL_S = 600  # 10 min — album metadata is effectively static
_REQUEST_TIMEOUT_S = 30.0
_QUERY_TIMEOUT_MS = 20_000
# Guard against a query that loses its cluster pruning and full-scans a table.
_MAX_BYTES_BILLED = 2 * 1024**3

# Country preference for canonical-release tie-breaks (worldwide / Europe / US / UK).
_PREFERRED_COUNTRIES = ["XW", "XE", "US", "GB"]

# Locale → ordered country preference for picking which pressing backs a variant.
# Biases the representative release toward the user's likely region; falls back to
# the worldwide default for anything unmapped. Keyed by the language part of the
# locale (region suffix is ignored, e.g. "en-US" → "en").
_LOCALE_COUNTRY_PREFS: Dict[str, List[str]] = {
    "en": ["GB", "US", "AU", "CA", "IE", "NZ"],
    "es": ["ES", "MX", "AR"],
    "fr": ["FR", "CA", "BE"],
    "de": ["DE", "AT", "CH"],
    "it": ["IT"],
    "pt": ["PT", "BR"],
    "nl": ["NL", "BE"],
    "sv": ["SE"],
    "ja": ["JP"],
    "ko": ["KR"],
    "zh": ["CN", "TW", "HK"],
}


def country_prefs_for_locale(locale: Optional[str]) -> List[str]:
    """Map a next-intl locale to an ordered country preference list.

    Returns a copy of the worldwide default (``_PREFERRED_COUNTRIES``) for an
    empty/unknown locale so callers can mutate freely.
    """
    if not locale:
        return list(_PREFERRED_COUNTRIES)
    base = re.split(r"[-_]", locale)[0].lower()
    return list(_LOCALE_COUNTRY_PREFS.get(base, _PREFERRED_COUNTRIES))

# "Extra" track qualifier keywords. Matched only inside (), [], or after " - "
# so we don't misfire on legitimate titles that happen to contain a word.
_EXTRA_KEYWORDS = [
    "live",
    "remix",
    "remixed",
    "bonus",
    "demo",
    "acoustic",
    "instrumental",
    "rehearsal",
    "alternate",
    "alternative",
    "radio edit",
    "single edit",
    "extended",
    "re-recorded",
    "rerecorded",
    "session",
    "outtake",
]
_EXTRA_KEYWORDS_RE = "|".join(re.escape(k) for k in _EXTRA_KEYWORDS)
# A qualifier segment: (...keyword...), [...keyword...], or " - ...keyword..."
_QUALIFIER_RE = re.compile(
    r"(?:[\(\[][^\)\]]*\b(?:" + _EXTRA_KEYWORDS_RE + r")\b[^\)\]]*[\)\]])"
    r"|(?:\s[-–]\s[^\(\[]*\b(?:" + _EXTRA_KEYWORDS_RE + r")\b)",
    re.IGNORECASE,
)


def classify_extra(title: str, disambiguation: str = "") -> tuple[bool, str]:
    """Return (is_extra, reason). Conservative: only flags clear qualifiers."""
    disamb = (disambiguation or "").strip().lower()
    if disamb:
        for kw in _EXTRA_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", disamb):
                return True, disamb
    m = _QUALIFIER_RE.search(title or "")
    if m:
        return True, m.group(0).strip(" -–()[]")
    return False, ""


def normalize_name(s: str) -> str:
    """Python twin of karaoke-decide's ``norm()`` SQL (``mb_artists*.name_normalized``):
    lowercase, every char outside ``[a-z0-9 ]`` → space, spaces collapsed, trimmed.
    Not accent-folded, matching those columns."""
    return re.sub(r" +", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def _decode_value(field: Dict[str, Any], value: Any) -> Any:
    if value is None:
        return None
    if field.get("mode") == "REPEATED":
        item = {**field, "mode": "NULLABLE"}
        return [_decode_value(item, v.get("v")) for v in value]
    if field.get("type") in ("INTEGER", "INT64"):
        return int(value)
    if field.get("type") in ("BOOLEAN", "BOOL"):
        return value == "true"
    return value


def _decode_rows(schema: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fields = schema.get("fields") or []
    return [
        {f["name"]: _decode_value(f, cell.get("v")) for f, cell in zip(fields, row.get("f") or [])}
        for row in rows
    ]


def _param(name: str, value: Any) -> Dict[str, Any]:
    if isinstance(value, list):
        return {
            "name": name,
            "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
            "parameterValue": {"arrayValues": [{"value": v} for v in value]},
        }
    return {"name": name, "parameterType": {"type": "STRING"}, "parameterValue": {"value": value}}


class MusicBrainzService:
    """Async reader for our BigQuery MusicBrainz mirror."""

    def __init__(self) -> None:
        self._cache: Dict[str, tuple[float, Any]] = {}
        self._credentials = None

    # -- BigQuery plumbing -------------------------------------------------

    def _access_token(self) -> str:
        # Sync (google-auth refresh does blocking HTTP) — call via asyncio.to_thread.
        import google.auth
        import google.auth.transport.requests

        if self._credentials is None:
            self._credentials, _ = google.auth.default(scopes=_BQ_SCOPES)
        if not self._credentials.valid:
            self._credentials.refresh(google.auth.transport.requests.Request())
        return self._credentials.token

    async def _query(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        cache_key = sql + repr(sorted(params.items()))
        cached = self._cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
            return cached[1]
        token = await asyncio.to_thread(self._access_token)
        body = {
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "NAMED",
            "queryParameters": [_param(k, v) for k, v in params.items()],
            "location": "US",
            "timeoutMs": _QUERY_TIMEOUT_MS,
            "maximumBytesBilled": str(_MAX_BYTES_BILLED),
        }
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = await client.post(_BQ_QUERY_URL, json=body, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            raise RuntimeError(f"BigQuery MusicBrainz query failed ({resp.status_code}): {resp.text[:500]}")
        data = resp.json()
        if not data.get("jobComplete"):
            raise RuntimeError("BigQuery MusicBrainz query timed out")
        if data.get("pageToken"):
            # Every query here is small and LIMITed; a second page means a bad query, not data to fetch.
            logger.warning("BigQuery MusicBrainz query returned more than one page; using the first")
        rows = _decode_rows(data.get("schema") or {}, data.get("rows") or [])
        self._cache[cache_key] = (time.monotonic(), rows)
        return rows

    # -- Public API --------------------------------------------------------

    async def search_artists(self, name: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Search artists by name: exact (incl. a leading "The") → prefix → substring,
        then by popularity (Spotify, 50 when unmapped)."""
        q = normalize_name(name)
        if not q:
            return []
        rows = await self._query(
            f"""
            SELECT artist_mbid, artist_name, disambiguation, artist_type, area_name, match_rank
            FROM (
              SELECT *,
                CASE
                  WHEN name_normalized = @q OR name_normalized = CONCAT('the ', @q) THEN 0
                  WHEN STARTS_WITH(name_normalized, @q) THEN 1
                  ELSE 2
                END AS match_rank
              FROM `{MB_DATASET}.mb_artists_normalized`
              WHERE STRPOS(name_normalized, @q) > 0
            )
            ORDER BY match_rank, popularity DESC, artist_name
            LIMIT {int(limit)}
            """,
            {"q": q},
        )
        return [
            {
                "mbid": r["artist_mbid"],
                "name": r["artist_name"],
                "disambiguation": r.get("disambiguation") or "",
                "type": r.get("artist_type"),
                "country": r.get("area_name"),
                "score": 100 - 10 * r["match_rank"],
            }
            for r in rows
        ]

    async def get_albums(self, artist_mbid: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List an artist's albums (release-groups), studio albums first, then by date."""
        rows = await self._query(
            f"""
            SELECT release_group_mbid, title, primary_type, secondary_types, first_release_date
            FROM `{MB_DATASET}.mb_release_groups`
            WHERE primary_type IN ('Album', 'EP')
              AND artist_credit_id IN (
                SELECT artist_credit_id FROM `{MB_DATASET}.mb_artist_credit_artists`
                WHERE artist_mbid = @artist_mbid)
            ORDER BY first_release_date IS NULL, first_release_date
            LIMIT {int(limit)}
            """,
            {"artist_mbid": artist_mbid},
        )
        groups = []
        for rg in rows:
            secondary = rg.get("secondary_types") or []
            groups.append(
                {
                    "release_group_mbid": rg["release_group_mbid"],
                    "title": rg["title"],
                    "primary_type": rg.get("primary_type"),
                    "secondary_types": secondary,
                    "first_release_date": rg.get("first_release_date") or "",
                    "is_studio": len(secondary) == 0,
                }
            )
        # Studio albums first, then earliest release date.
        groups.sort(key=lambda g: (not g["is_studio"], g["first_release_date"] or "9999"))
        return groups

    async def _list_editions(self, release_group_mbid: str) -> List[Dict[str, Any]]:
        rows = await self._query(
            f"""
            SELECT release_mbid, title, status, release_date, countries, packaging, disambiguation, track_count
            FROM `{MB_DATASET}.mb_releases`
            WHERE release_group_mbid = @rg
            ORDER BY release_date IS NULL, release_date, release_mbid
            LIMIT 500
            """,
            {"rg": release_group_mbid},
        )
        return [self._edition(r) for r in rows]

    @staticmethod
    def _edition(r: Dict[str, Any]) -> Dict[str, Any]:
        countries = r.get("countries") or []
        return {
            "release_mbid": r["release_mbid"],
            "title": r.get("title"),
            "status": r.get("status"),
            "date": r.get("release_date") or "",
            # A release can have several release events; the first is what we show.
            "country": countries[0] if countries else None,
            "countries": countries,
            "packaging": r.get("packaging"),
            "disambiguation": r.get("disambiguation") or "",
            "track_count": r.get("track_count") or 0,
        }

    @staticmethod
    def pick_canonical_edition(
        editions: List[Dict[str, Any]],
        preferred_countries: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Pick the standard edition: official, modal track count, earliest, preferred country.

        Choosing the most common track count avoids deluxe/remaster editions that
        tack bonus tracks on the end. ``preferred_countries`` overrides the default
        worldwide-first tie-break (used for locale-aware selection).
        """
        if not editions:
            return None
        prefs = preferred_countries or _PREFERRED_COUNTRIES
        official = [e for e in editions if e.get("status") == "Official"] or editions
        with_tracks = [e for e in official if e.get("track_count", 0) > 0] or official
        counts = Counter(e["track_count"] for e in with_tracks if e.get("track_count", 0) > 0)
        if counts:
            standard = counts.most_common(1)[0][0]
            candidates = [e for e in with_tracks if e["track_count"] == standard]
        else:
            candidates = with_tracks

        def country_rank(e: Dict[str, Any]) -> int:
            # Best-ranked of the edition's release-event countries.
            countries = e.get("countries") or ([e["country"]] if e.get("country") else [])
            return min((prefs.index(c) for c in countries if c in prefs), default=len(prefs))

        candidates.sort(key=lambda e: (e.get("date") or "9999", country_rank(e)))
        return candidates[0]

    @staticmethod
    def build_variants(
        editions: List[Dict[str, Any]],
        preferred_countries: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Collapse a release-group's editions into distinct tracklist *variants*.

        Country pressings are irrelevant for karaoke (we only need the track names),
        so editions are grouped by ``track_count`` — within one release-group, same
        count ≈ same songs. Each variant exposes a representative release (earliest +
        preferred-country), the earliest year, how many pressings collapsed in, and a
        label/delta relative to the "Original" (earliest) variant. Editions without a
        track count are dropped. See
        ``docs/archive/2026-06-22-bulk-edition-collapse-and-community-versions-design.md``.
        """
        if not editions:
            return []
        official = [e for e in editions if e.get("status") == "Official"] or editions
        with_tracks = [e for e in official if (e.get("track_count") or 0) > 0]
        if not with_tracks:
            return []

        groups: Dict[int, List[Dict[str, Any]]] = {}
        for e in with_tracks:
            groups.setdefault(e["track_count"], []).append(e)

        variants: List[Dict[str, Any]] = []
        for track_count, group in groups.items():
            rep = MusicBrainzService.pick_canonical_edition(group, preferred_countries)
            earliest = min((e.get("date") or "9999") for e in group)
            year = earliest[:4] if earliest and earliest != "9999" else ""
            variants.append(
                {
                    "representative_release_mbid": rep["release_mbid"],
                    "track_count": track_count,
                    "year": year,
                    "pressing_count": len(group),
                    "_earliest": earliest,
                }
            )

        # Original = earliest release, then most pressings, then fewest tracks.
        variants.sort(key=lambda v: (v["_earliest"], -v["pressing_count"], v["track_count"]))
        original = variants[0]
        original_tc = original["track_count"]
        for v in variants:
            v["delta_vs_original"] = v["track_count"] - original_tc
            v["label"] = "Original" if v is original else "Reissue"
            del v["_earliest"]

        # Display order: Original first, then by year ascending, then track count.
        variants.sort(key=lambda v: (v["label"] != "Original", v["year"] or "9999", v["track_count"]))
        return variants

    async def get_release_tracklist(
        self, release_mbid: str, release_group_mbid: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch a release's tracklist with per-track extra detection.

        ``release_group_mbid`` lets both reads prune on their cluster key; without it
        we look it up first (one ~0.2 GB scan of ``mb_releases``)."""
        if not release_group_mbid:
            found = await self._query(
                f"SELECT release_group_mbid FROM `{MB_DATASET}.mb_releases` WHERE release_mbid = @r LIMIT 1",
                {"r": release_mbid},
            )
            if not found:
                raise LookupError(f"Unknown release {release_mbid}")
            release_group_mbid = found[0]["release_group_mbid"]
        params = {"rg": release_group_mbid, "r": release_mbid}
        release_rows, track_rows = await asyncio.gather(
            self._query(
                f"""
                SELECT release_mbid, title, release_date, countries
                FROM `{MB_DATASET}.mb_releases`
                WHERE release_group_mbid = @rg AND release_mbid = @r
                """,
                params,
            ),
            self._query(
                f"""
                SELECT medium_position, track_position, title, recording_mbid,
                       recording_disambiguation, length_ms
                FROM `{MB_DATASET}.mb_release_tracks`
                WHERE release_group_mbid = @rg AND release_mbid = @r
                ORDER BY medium_position, track_position
                LIMIT 1000
                """,
                params,
            ),
        )
        if not release_rows:
            raise LookupError(f"Unknown release {release_mbid}")
        release = release_rows[0]
        tracks: List[Dict[str, Any]] = []
        for t in track_rows:
            title = t.get("title") or ""
            disamb = t.get("recording_disambiguation") or ""
            is_extra, reason = classify_extra(title, disamb)
            tracks.append(
                {
                    "position": t.get("track_position"),
                    "title": title,
                    "recording_mbid": t.get("recording_mbid"),
                    "disambiguation": disamb,
                    "length_ms": t.get("length_ms"),
                    "is_extra": is_extra,
                    "extra_reason": reason,
                }
            )
        countries = release.get("countries") or []
        return {
            "release_mbid": release["release_mbid"],
            "title": release.get("title"),
            "date": release.get("release_date") or "",
            "country": countries[0] if countries else None,
            "tracks": tracks,
        }

    async def get_album_tracklist(
        self,
        release_group_mbid: str,
        preferred_countries: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Resolve the canonical edition of an album and return its tracklist, the raw
        editions, and the collapsed tracklist ``variants``. ``preferred_countries``
        biases representative selection (locale-aware)."""
        editions = await self._list_editions(release_group_mbid)
        canonical = self.pick_canonical_edition(editions, preferred_countries)
        variants = self.build_variants(editions, preferred_countries)
        if not canonical:
            return {
                "release_mbid": None,
                "title": None,
                "tracks": [],
                "editions": editions,
                "variants": variants,
                "selected_variant_mbid": None,
            }
        tracklist = await self.get_release_tracklist(canonical["release_mbid"], release_group_mbid)
        tracklist["editions"] = editions
        tracklist["canonical_release_mbid"] = canonical["release_mbid"]
        tracklist["variants"] = variants
        tracklist["selected_variant_mbid"] = canonical["release_mbid"]
        return tracklist


_service: Optional[MusicBrainzService] = None


def get_musicbrainz_service() -> MusicBrainzService:
    global _service
    if _service is None:
        _service = MusicBrainzService()
    return _service
