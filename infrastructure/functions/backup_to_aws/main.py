"""
Backup to AWS Cloud Function.

Nightly backup pipeline:
1. Firestore export to GCS staging
2. BigQuery export to GCS staging (weekly/monthly schedule)
3. GCS job files delta sync to staging
4. Secret Manager export (encrypted with sealed-box public key) to staging
5. Upload staging files to S3
6. Discord alert

Triggered by Cloud Scheduler at 1:00 AM ET daily.
"""

import datetime
import json
import logging
import os

import functions_framework

from discord_alert import send_alert
from firestore_export import export_firestore
from bigquery_export import export_bigquery_tables
from gcs_sync import sync_gcs_to_staging
from secrets_export import export_secrets
from s3_upload import upload_staging_to_s3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "nomadkaraoke-backup-staging")
S3_BUCKET = os.environ.get("S3_BUCKET", "nomadkaraoke-backup")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "nomadkaraoke")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BACKUP_ENCRYPTION_PUBKEY = os.environ.get("BACKUP_ENCRYPTION_PUBKEY", "")


@functions_framework.http
def backup_to_aws(request):
    """Main entry point for the backup Cloud Function."""
    # Quarterly drill reminder — Cloud Scheduler hits this with ?mode=drill_reminder.
    # We just post Discord and exit without touching the backup pipeline.
    if request.args.get("mode") == "drill_reminder":
        if DISCORD_WEBHOOK_URL:
            send_alert(
                webhook_url=DISCORD_WEBHOOK_URL,
                title="📋 Quarterly DR restore drill due",
                fields=[
                    {"name": "What", "value": "Decrypt yesterday's secrets backup + restore one Firestore collection + one BigQuery table to a side database, confirm the chain works.", "inline": False},
                    {"name": "Runbook", "value": "https://github.com/nomadkaraoke/karaoke-gen/blob/main/docs/DISASTER-RECOVERY.md#quarterly-restore-drill", "inline": False},
                ],
                success=True,
            )
        return json.dumps({"status": "drill_reminder_sent"}), 200

    today = datetime.date.today()
    date_str = today.isoformat()
    results = {}
    errors = []

    logger.info(f"Starting backup for {date_str}")

    # Step 1: Firestore export (nightly)
    try:
        results["firestore"] = export_firestore(
            project=GCP_PROJECT,
            staging_bucket=STAGING_BUCKET,
            date_str=date_str,
        )
    except Exception as e:
        logger.error(f"Firestore export failed: {e}")
        errors.append(f"Firestore: {e}")

    # Step 2: BigQuery export (weekly on Sundays, monthly on 1st)
    try:
        bq_results = export_bigquery_tables(
            project=GCP_PROJECT,
            staging_bucket=STAGING_BUCKET,
            date_str=date_str,
            day_of_week=today.weekday(),
            day_of_month=today.day,
        )
        results["bigquery"] = bq_results
    except Exception as e:
        logger.error(f"BigQuery export failed: {e}")
        errors.append(f"BigQuery: {e}")

    # Step 3: GCS delta sync (nightly) — primary job-files bucket
    try:
        results["gcs_sync"] = sync_gcs_to_staging(
            source_bucket="karaoke-gen-storage-nomadkaraoke",
            staging_bucket=STAGING_BUCKET,
            staging_prefix="gcs/job-files/",
        )
    except Exception as e:
        logger.error(f"GCS sync failed: {e}")
        errors.append(f"GCS sync: {e}")

    # Step 3b: GCS delta sync — nomadkaraoke-kn-data (small, irreplaceable
    # internal sync data from KaraokeNerds API; not publicly regenerable).
    # Uses [""] to walk the whole bucket since it has no top-level prefix structure.
    try:
        results["gcs_sync_kn"] = sync_gcs_to_staging(
            source_bucket="nomadkaraoke-kn-data",
            staging_bucket=STAGING_BUCKET,
            staging_prefix="gcs/kn-data/",
            sync_prefixes=[""],
        )
    except Exception as e:
        logger.error(f"GCS kn-data sync failed: {e}")
        errors.append(f"GCS kn-data sync: {e}")

    # Step 4: Secrets export (nightly, encrypted with sealed-box public key)
    try:
        results["secrets"] = export_secrets(
            project=GCP_PROJECT,
            staging_bucket=STAGING_BUCKET,
            date_str=date_str,
            public_key_hex=BACKUP_ENCRYPTION_PUBKEY,
        )
    except Exception as e:
        logger.error(f"Secrets export failed: {e}")
        errors.append(f"Secrets: {e}")

    # Step 5: Upload to S3
    try:
        results["s3_upload"] = upload_staging_to_s3(
            staging_bucket=STAGING_BUCKET,
            s3_bucket=S3_BUCKET,
        )
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        errors.append(f"S3 upload: {e}")

    # Step 6: Discord alert
    success = len(errors) == 0
    fields = [
        {"name": "Date", "value": date_str, "inline": True},
        {"name": "Status", "value": "Success" if success else "FAILED", "inline": True},
    ]
    if errors:
        fields.append({"name": "Errors", "value": "\n".join(errors), "inline": False})
    for key, value in results.items():
        if isinstance(value, str):
            fields.append({"name": key, "value": value, "inline": True})

    if DISCORD_WEBHOOK_URL:
        send_alert(
            webhook_url=DISCORD_WEBHOOK_URL,
            title="Nightly Backup Report",
            fields=fields,
            success=success,
        )

    status_code = 200 if success else 500
    return json.dumps({"status": "ok" if success else "failed", "errors": errors}), status_code
