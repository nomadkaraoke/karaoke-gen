"""
Storage-retention pass as a Cloud Run Job (``storage-retention-job``).

The API endpoint ``POST /api/internal/storage-retention/run`` (Cloud Scheduler,
daily) starts this job instead of doing the work itself: the backend service
throttles CPU outside requests and requests through Cloudflare time out after
100s, while a full pass takes minutes. See backend/services/storage_retention.py.

Usage:
    python -m backend.workers.storage_retention_worker --dry-run true \\
        [--max-jobs N] [--include-orphans] [--report-path storage-retention/reports/x.json]
"""
import argparse
import logging
import sys


def _bool(value: str) -> bool:
    return str(value).strip().lower() not in ("false", "0", "no")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Storage-retention pass")
    parser.add_argument("--dry-run", type=_bool, default=True)
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--include-orphans", action="store_true")
    parser.add_argument("--report-path", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    from backend.services.storage_retention import StorageRetentionService

    try:
        report = StorageRetentionService().run(
            dry_run=args.dry_run, max_jobs=args.max_jobs,
            include_orphans=args.include_orphans, report_path=args.report_path,
        )
    except Exception:
        logging.getLogger(__name__).exception("STORAGE_RETENTION job crashed")
        return 1
    # Non-zero exit marks the Cloud Run execution failed (visible in the
    # console/alerts) when the report couldn't be written or any job errored.
    return 1 if (report.get("report_write_error") or report.get("errors")) else 0


if __name__ == "__main__":
    sys.exit(main())
