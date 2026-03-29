# Disaster Recovery Runbook

**Last updated:** 2026-03-29
**Design doc:** `docs/archive/2026-03-27-business-continuity-design.md`

## Quick Reference

| Data | Backup Location | Recovery Method | RPO |
|------|----------------|-----------------|-----|
| Firestore | S3 `firestore/YYYY-MM-DD/` | Import to new GCP project | 24h |
| BigQuery (weekly) | S3 `bigquery/daily-refresh/` | Load Parquet files | 7 days |
| BigQuery (monthly) | S3 `bigquery/musicbrainz/` | Load Parquet files | 30 days |
| BigQuery (Spotify) | S3 `bigquery/spotify/` | Load from Glacier Deep Archive | N/A (static) |
| GCS job files | S3 `gcs/job-files/` | Sync to new bucket | 24h |
| Secrets | AWS Secrets Manager | Decrypt with KMS, configure | On-change |

## Scenario 1: Accidental Data Deletion (GCP Still Available)

### Firestore Recovery

**Option A — PITR (fastest, within 7 days):**
```bash
gcloud firestore databases restore \
  --source-database="(default)" \
  --destination-database="(default)-restored" \
  --snapshot-time="2026-03-28T12:00:00Z" \
  --project=nomadkaraoke
```

**Option B — Import from S3 backup:**
```bash
# Download latest export from S3
aws s3 sync s3://nomadkaraoke-backup/firestore/LATEST_DATE/ /tmp/firestore-restore/ \
  --profile nomadkaraoke-backup

# Upload to GCS
gsutil -m cp -r /tmp/firestore-restore/ gs://nomadkaraoke-backup-staging/firestore-import/

# Import
gcloud firestore import gs://nomadkaraoke-backup-staging/firestore-import/ --project=nomadkaraoke
```

### BigQuery Recovery

**Option A — Time Travel (within 7 days):**
```sql
-- Query historical data
SELECT * FROM `nomadkaraoke.karaoke_decide.TABLE_NAME`
FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR);

-- Restore to a new table
CREATE TABLE `nomadkaraoke.karaoke_decide.TABLE_NAME_restored` AS
SELECT * FROM `nomadkaraoke.karaoke_decide.TABLE_NAME`
FOR SYSTEM_TIME AS OF TIMESTAMP("2026-03-28 12:00:00 UTC");
```

**Option B — Load from S3 backup:**
```bash
# Download Parquet exports
aws s3 sync s3://nomadkaraoke-backup/bigquery/daily-refresh/LATEST_DATE/ /tmp/bq-restore/ \
  --profile nomadkaraoke-backup

# Load each table
bq load --source_format=PARQUET \
  nomadkaraoke:karaoke_decide.TABLE_NAME \
  /tmp/bq-restore/TABLE_NAME/*.parquet
```

### GCS Recovery

**Option A — Object versioning (within 30 days):**
```bash
# List versions of a deleted/overwritten object
gsutil ls -a gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/

# Restore a specific version
gsutil cp gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/file.flac#VERSION \
  gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/file.flac
```

**Option B — Restore from S3:**
```bash
aws s3 sync s3://nomadkaraoke-backup/gcs/job-files/jobs/JOB_ID/ /tmp/gcs-restore/ \
  --profile nomadkaraoke-backup

gsutil -m cp -r /tmp/gcs-restore/ gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/
```

## Scenario 2: Complete GCP Project Loss

### Prerequisites

- AWS credentials from password manager
- AWS KMS key ARN from password manager
- All GitHub repos (code + IaC)

### Step-by-Step Recovery

**1. Create new GCP project**
```bash
gcloud projects create nomadkaraoke-v2 --name="Nomad Karaoke"
gcloud config set project nomadkaraoke-v2
gcloud services enable firestore.googleapis.com run.googleapis.com \
  cloudfunctions.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com bigquery.googleapis.com \
  cloudtasks.googleapis.com artifactregistry.googleapis.com
```

**2. Retrieve AWS credentials**

Get from password manager: AWS access key, secret key, KMS key ARN.

**3. Restore Firestore**
```bash
aws s3 sync s3://nomadkaraoke-backup/firestore/LATEST_DATE/ /tmp/firestore-restore/ \
  --profile nomadkaraoke-backup
gsutil -m cp -r /tmp/firestore-restore/ gs://NEW_BUCKET/firestore-import/
gcloud firestore import gs://NEW_BUCKET/firestore-import/ --project=nomadkaraoke-v2
```

**4. Restore BigQuery**
```bash
# Create dataset
bq mk --dataset nomadkaraoke-v2:karaoke_decide

# Download and load each table
for prefix in daily-refresh musicbrainz; do
  aws s3 sync s3://nomadkaraoke-backup/bigquery/$prefix/LATEST_DATE/ /tmp/bq-$prefix/ \
    --profile nomadkaraoke-backup
  for dir in /tmp/bq-$prefix/*/; do
    table=$(basename $dir)
    bq load --source_format=PARQUET nomadkaraoke-v2:karaoke_decide.$table "$dir*.parquet"
  done
done
```

**Spotify tables (Glacier Deep Archive):** Retrieval takes 12-48 hours.
```bash
# Initiate restore (bulk = cheapest, ~$150 for 1TB)
aws s3api restore-object --bucket nomadkaraoke-backup \
  --key "bigquery/spotify/TABLE/part-000.parquet" \
  --restore-request '{"Days":7,"GlacierJobParameters":{"Tier":"Bulk"}}' \
  --profile nomadkaraoke-backup
# Wait 12-48 hours, then download and load
```

**5. Restore GCS files**
```bash
# Create new bucket
gsutil mb -l US-CENTRAL1 gs://karaoke-gen-storage-nomadkaraoke-v2/

# Sync from S3
aws s3 sync s3://nomadkaraoke-backup/gcs/job-files/ /tmp/gcs-restore/ \
  --profile nomadkaraoke-backup
gsutil -m cp -r /tmp/gcs-restore/ gs://karaoke-gen-storage-nomadkaraoke-v2/
```

**6. Restore secrets**
```bash
# Retrieve all secrets from AWS Secrets Manager
aws secretsmanager get-secret-value --secret-id nomadkaraoke-gcp-secrets \
  --profile nomadkaraoke-backup | jq -r '.SecretString' > /tmp/secrets.json

# Create each secret in new project
python3 -c "
import json
with open('/tmp/secrets.json') as f:
    secrets = json.load(f)
for name, value in secrets.items():
    print(f'echo -n \"{value}\" | gcloud secrets create {name} --data-file=- --project=nomadkaraoke-v2')
"
# Review and run the generated commands
rm /tmp/secrets.json
```

**7. Rebuild infrastructure**
```bash
cd karaoke-gen/infrastructure
# Update config.py with new project ID
pulumi config set gcp:project nomadkaraoke-v2
pulumi up
```

**8. Deploy services**
```bash
# Push to GitHub triggers CI/CD, or deploy manually:
gcloud run deploy karaoke-backend --source=. --project=nomadkaraoke-v2
```

**9. Update DNS (Cloudflare)**

Update DNS records to point to new Cloud Run URLs. See `docs/archive/2026-03-29-external-services-config.md`.

**10. Re-register OAuth apps**

Re-create OAuth apps at Spotify, Google, Dropbox with redirect URIs from `docs/archive/2026-03-29-external-services-config.md`.

## Cost Estimates for Recovery

| Action | Cost |
|--------|------|
| S3 Glacier Deep Archive retrieval (1 TB, bulk) | ~$150 |
| S3 Glacier Deep Archive retrieval (1 TB, standard) | ~$750 |
| S3 data transfer out to GCP | ~$0.09/GB |
| Full Spotify dataset retrieval + transfer (~1 TB) | ~$250 total |
