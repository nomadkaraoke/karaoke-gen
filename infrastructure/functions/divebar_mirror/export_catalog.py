"""Export the divebar_catalog BigQuery table to a public GCS snapshot.

The KJ Controller (kjbox) mirrors all three remote catalogs into a local
SQLite database so live-show searches never depend on venue Wi-Fi or
BigQuery latency. The KaraokeNerds catalogs already have GCS exports
(kn-data-sync); this module produces the missing third one.

The export runs at the end of every index build — AFTER the staging MERGE —
because ``gcs_path`` lives only in the main table (the file-sync VM sets it);
dumping the freshly-parsed Drive rows would report ``in_gcs=false`` for every
mirrored file.

Written to the PUBLIC divebar files bucket: this data is already publicly
queryable row-by-row via the divebar-lookup Cloud Function's ``search``
action, so a bulk snapshot adds no new exposure.
"""

import gzip
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

EXPORT_BUCKET = "nomadkaraoke-divebar-files"
EXPORT_OBJECT = "exports/divebar-catalog-latest.json.gz"

# Same column set the divebar-lookup CF's `search` action returns, so the
# kjbox mirror is a drop-in data source for the existing result shapes.
_EXPORT_SQL = """
    SELECT
        file_id,
        brand,
        brand_code,
        artist,
        title,
        filename,
        format,
        file_size,
        drive_path,
        (gcs_path IS NOT NULL) AS in_gcs
    FROM `{project}.karaoke_decide.divebar_catalog`
"""


def export_catalog_to_gcs(project_id: str) -> dict:
    """Snapshot divebar_catalog to gzipped NDJSON in the public files bucket.

    Returns ``{"rows": n, "gcs_uri": ...}``. Raises on failure — the caller
    treats the export as best-effort and must not fail the index build on it.
    """
    from google.cloud import bigquery, storage

    bq = bigquery.Client(project=project_id)
    rows_iter = bq.query(_EXPORT_SQL.format(project=project_id)).result()

    buf = bytearray()
    count = 0
    # mtime=0 keeps the gzip output deterministic for identical content, so
    # the kjbox sync's content-hash check skips rebuilds when nothing changed.
    with gzip.GzipFile(fileobj=_Appendable(buf), mode="wb", mtime=0) as gz:
        for row in rows_iter:
            gz.write(json.dumps(dict(row), default=str).encode("utf-8"))
            gz.write(b"\n")
            count += 1

    client = storage.Client(project=project_id)
    blob = client.bucket(EXPORT_BUCKET).blob(EXPORT_OBJECT)
    blob.metadata = {
        "row_count": str(count),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    blob.upload_from_string(bytes(buf), content_type="application/gzip")

    gcs_uri = f"gs://{EXPORT_BUCKET}/{EXPORT_OBJECT}"
    logger.info("Exported %d catalog rows to %s (%d bytes gz)", count, gcs_uri, len(buf))
    return {"rows": count, "gcs_uri": gcs_uri}


class _Appendable:
    """Minimal writable file-object over a bytearray for GzipFile."""

    def __init__(self, buf: bytearray):
        self._buf = buf

    def write(self, data: bytes) -> int:
        self._buf.extend(data)
        return len(data)

    def flush(self) -> None:  # pragma: no cover - GzipFile may call it
        pass
