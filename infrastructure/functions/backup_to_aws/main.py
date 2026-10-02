"""
Backup to AWS Cloud Function.

Nightly backup pipeline:
1. Firestore export to GCS staging (nightly; uploaded to S3 weekly on Sundays)
2. BigQuery export to GCS staging (weekly/monthly schedule)
3. GCS delta sync of nomadkaraoke-kn-data to staging. (Job files from the
   karaoke-gen-storage bucket are NOT copied off-site any more — stopped
   2026-10-01 to save ~$15/mo egress; they rely on GCS object versioning
   + soft-delete only. See docs/DISASTER-RECOVERY.md.)
4. Secret Manager export (encrypted with sealed-box public key) to staging
4b. Git repos backup — bundle repos under the configured GitHub owners to
    staging (weekly on Sundays, incremental: only repos pushed since their
    bundle last reached S3; full re-bundle on the first Sunday of each month;
    survives loss of GitHub access, e.g. an account ban)
5. Upload staging files to S3 (Firestore export held back except Sundays)
6. Discord alert

Triggered by Cloud Scheduler at 1:00 AM ET daily.

Firestore is the bulk of the cross-cloud egress. Exporting it nightly to GCS
keeps a 1-day local restore point, while uploading to S3 only weekly keeps a
1-week off-site RPO at ~1/7th the egress cost.
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
from git_repos_export import export_git_repos, get_github_token
from secrets_export import export_secrets
from s3_upload import get_s3_client, list_s3_objects, upload_staging_to_s3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "nomadkaraoke-backup-staging")
S3_BUCKET = os.environ.get("S3_BUCKET", "nomadkaraoke-backup")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "nomadkaraoke")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BACKUP_ENCRYPTION_PUBKEY = os.environ.get("BACKUP_ENCRYPTION_PUBKEY", "")
# GitHub owners (comma-sep) whose repos get bundled to S3. The PAT itself is
# read at runtime from Secret Manager ("github-backup-token"); if it has no
# value the git-repo step is skipped and the rest of the pipeline still runs.
GIT_BACKUP_OWNERS = os.environ.get("GIT_BACKUP_OWNERS", "")


GIT_REPOS_PREFIX = "git-repos/"
JOB_FILES_PREFIX = "gcs/job-files/"


def _run_git_repos_backup(full_refresh: bool) -> str:
    """Stage bundles for repos changed since their S3 copy (or all, if
    ``full_refresh``). Returns a summary string; raises on systemic failure."""
    github_token = get_github_token(GCP_PROJECT)
    if not github_token:
        logger.warning("github-backup-token has no value — skipping git repo backup")
        return "skipped (no token)"

    existing = None
    if not full_refresh:
        try:
            existing = list_s3_objects(get_s3_client(GCP_PROJECT), S3_BUCKET, GIT_REPOS_PREFIX)
        except Exception as e:  # noqa: BLE001 — can't see S3 -> safe fallback is a full bundle
            logger.warning(f"Could not list existing git bundles in S3 ({e}); bundling all repos")

    owners = [o.strip() for o in GIT_BACKUP_OWNERS.split(",") if o.strip()] or None
    return export_git_repos(
        staging_bucket=STAGING_BUCKET,
        github_token=github_token,
        owners=owners,
        existing_backups=existing,
        full_refresh=full_refresh,
    )


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

    # Manual git-repo-only run (e.g. to verify a change or force a refresh
    # without re-running the Firestore export, which collides on same-day paths):
    #   ?mode=git_repos            incremental
    #   ?mode=git_repos&full=1     re-bundle everything
    if request.args.get("mode") == "git_repos":
        try:
            summary = _run_git_repos_backup(full_refresh=request.args.get("full") == "1")
            upload = upload_staging_to_s3(
                staging_bucket=STAGING_BUCKET,
                s3_bucket=S3_BUCKET,
                include_prefixes=[GIT_REPOS_PREFIX],
            )
            return json.dumps({"status": "ok", "git_repos": summary, "s3_upload": upload}), 200
        except Exception as e:
            logger.error(f"Manual git repos backup failed: {e}")
            return json.dumps({"status": "failed", "errors": [f"Git repos: {e}"]}), 500

    today = datetime.date.today()
    date_str = today.isoformat()
    results = {}
    errors = []

    # The Firestore export runs nightly (daily local restore point in GCS) but
    # is only pushed off-site to S3 weekly, on Sundays — matching the BigQuery
    # weekly cadence. This is the bulk of the cross-cloud egress, so weekly
    # off-site keeps a 1-week off-site RPO while a 1-day local RPO is retained.
    firestore_to_s3_today = today.weekday() == 6  # Sunday
    git_repos_today = today.weekday() == 6  # Sunday
    git_full_refresh_today = git_repos_today and today.day <= 7  # first Sunday of the month

    logger.info(f"Starting backup for {date_str} (firestore->S3: {firestore_to_s3_today})")

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

    # Step 3 (removed 2026-10-01): the nightly delta sync of job files
    # (karaoke-gen-storage-nomadkaraoke -> gcs/job-files/) was the bulk of the
    # remaining cross-cloud egress (~$15/mo) for regenerable outputs. Job files
    # now rely on GCS object versioning + soft-delete only; existing S3 copies
    # under gcs/job-files/ are left in place. See docs/DISASTER-RECOVERY.md.

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

    # Step 4b: Git repos backup (weekly, Sundays). Bundles repos so code + full
    # history survives loss of GitHub access (e.g. an account ban). Incremental:
    # only repos pushed since their bundle last landed in S3 are re-cloned and
    # re-uploaded (cross-cloud egress was a nightly full re-upload of ~120
    # bundles). The first Sunday of each month re-bundles everything as a
    # belt-and-braces refresh. Cleanly skipped if no token is set.
    if git_repos_today:
        try:
            results["git_repos"] = _run_git_repos_backup(full_refresh=git_full_refresh_today)
        except Exception as e:
            logger.error(f"Git repos backup failed: {e}")
            errors.append(f"Git repos: {e}")
    else:
        results["git_repos"] = "skipped (weekly, runs Sundays)"

    # Step 5: Upload to S3. On non-Sundays, hold the Firestore export back
    # (it stays in GCS staging as a daily local backup); it ships to S3 weekly.
    # git-repos/ uploads only in the same invocation as the git step: the
    # incremental check trusts that a bundle's S3 LastModified is within
    # _UPLOAD_LAG of its clone. A bundle left in staging by a failed upload
    # must not ship on a later night (a push in between would then look backed
    # up); next Sunday's git step re-bundles it fresh instead.
    # gcs/job-files/ is never shipped any more (off-site job-file copy stopped
    # 2026-10-01) — also skip anything a pre-change run left in staging.
    exclude = [JOB_FILES_PREFIX]
    if not firestore_to_s3_today:
        exclude.append("firestore/")
    if not git_repos_today:
        exclude.append(GIT_REPOS_PREFIX)
    try:
        results["s3_upload"] = upload_staging_to_s3(
            staging_bucket=STAGING_BUCKET,
            s3_bucket=S3_BUCKET,
            exclude_prefixes=exclude,
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
