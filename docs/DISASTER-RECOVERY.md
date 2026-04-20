# Disaster Recovery Runbook

**Last updated:** 2026-04-20
**Design doc:** `docs/archive/2026-03-27-business-continuity-design.md`
**Implementation plan:** `docs/archive/2026-03-29-business-continuity-plan.md`
**External services reference:** `docs/archive/2026-03-29-external-services-config.md`

## Quick Reference

| Data | Backup Location | Recovery Method | RPO |
|------|----------------|-----------------|-----|
| Firestore | S3 `firestore/YYYY-MM-DD/` | Import to new GCP project | 24h |
| BigQuery (weekly) | S3 `bigquery/daily-refresh/` | Load Parquet files | 7 days |
| BigQuery (monthly) | S3 `bigquery/musicbrainz/` | Load Parquet files | 30 days |
| BigQuery (Spotify) | S3 `bigquery/spotify/` | Load from Glacier Deep Archive | N/A (static) |
| GCS job files | S3 `gcs/job-files/` | Sync to new bucket | 24h |
| Secret Manager | S3 `secrets/YYYY-MM-DD.bin` (sealed-box encrypted) | Decrypt with private key from KeepassXC | 24h |
| AWS credentials (function) | GCP Secret Manager `aws-backup-credentials` | Read directly | On-change |
| AWS credentials (offline) | KeepassXC — "Nomad Karaoke — DR backup AWS access" | Manual lookup | On-change |
| Backup decryption key | KeepassXC — "Nomad Karaoke — DR backup decryption key" | Manual lookup | One-time |

## Prerequisites — verify these BEFORE you need them

- [ ] KeepassXC contains "Nomad Karaoke — DR backup AWS access" (access key, secret key, region, S3 bucket name)
- [ ] KeepassXC contains "Nomad Karaoke — DR backup decryption key" (Curve25519 private key, 64 hex chars)
- [ ] GitHub repo `nomadkaraoke/karaoke-gen` has the secrets `AWS_BACKUP_READONLY_ACCESS_KEY_ID`, `AWS_BACKUP_READONLY_SECRET_ACCESS_KEY`, `DR_MONITOR_DISCORD_WEBHOOK` configured (powers the freshness monitor)
- [ ] You can decrypt yesterday's secrets backup locally (run the drill in § "Quarterly restore drill")
- [ ] Your KeepassXC database itself is backed up off-machine (cloud sync, second device, or printed paper recovery)

If any of these fail, fix them now — not during an incident.

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
# Download latest export from S3 (set creds first — see § "Setting AWS creds locally")
aws s3 sync s3://nomadkaraoke-backup/firestore/LATEST_DATE/ /tmp/firestore-restore/

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
aws s3 sync s3://nomadkaraoke-backup/bigquery/daily-refresh/LATEST_DATE/ /tmp/bq-restore/

# Load each table
bq load --source_format=PARQUET \
  nomadkaraoke:karaoke_decide.TABLE_NAME \
  /tmp/bq-restore/TABLE_NAME/*.parquet
```

### GCS Recovery

The primary bucket has **two** independent recovery paths. Pick whichever applies.

**Option A — GCS soft-delete (within 7 days, default-on, no special config):**
```bash
# List soft-deleted objects under a prefix
gcloud storage ls --soft-deleted gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/

# Restore a specific soft-deleted object (note the generation number from the listing)
gcloud storage restore gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/file.flac#GENERATION
```

**Option B — Object versioning (within 30 days, restores overwritten/deleted versions):**

Requires versioning to be enabled on the bucket — confirm with `gcloud storage buckets describe gs://karaoke-gen-storage-nomadkaraoke --format="value(versioning.enabled)"`. The Pulumi config in `infrastructure/modules/storage.py` enables it; if the live bucket reports `False`, run `pulumi up` to reconcile before relying on this path.

```bash
# List all versions (including non-current) of a path
gcloud storage ls -a gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/

# Copy a specific non-current generation back to live
gcloud storage cp gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/file.flac#GENERATION \
  gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/file.flac
```

**Option C — Restore from S3 (if A and B both miss the window):**
```bash
aws s3 sync s3://nomadkaraoke-backup/gcs/job-files/jobs/JOB_ID/ /tmp/gcs-restore/

gsutil -m cp -r /tmp/gcs-restore/ gs://karaoke-gen-storage-nomadkaraoke/jobs/JOB_ID/
```

### Secret Manager Recovery

**Option A — GCP Secret Manager versioning (Secret Manager keeps every version by default):**
```bash
# List versions
gcloud secrets versions list SECRET_NAME --project=nomadkaraoke

# Inspect any prior version
gcloud secrets versions access VERSION_NUMBER --secret=SECRET_NAME --project=nomadkaraoke

# Restore: create a new "latest" version with the old payload
gcloud secrets versions access VERSION_NUMBER --secret=SECRET_NAME --project=nomadkaraoke \
  | gcloud secrets versions add SECRET_NAME --data-file=- --project=nomadkaraoke
```

**Option B — Decrypt the encrypted S3 backup (use if a secret was deleted entirely):**
See § "Decrypting the secrets backup" below.

## Scenario 2: Complete GCP Project Loss

### Prerequisites

- AWS credentials from KeepassXC ("Nomad Karaoke — DR backup AWS access")
- Backup decryption key from KeepassXC ("Nomad Karaoke — DR backup decryption key")
- All GitHub repos (code + IaC)
- A working machine with Python 3.10+ and `pynacl` installable

### Step-by-Step Recovery

**1. Create new GCP project**
```bash
gcloud projects create nomadkaraoke-v2 --name="Nomad Karaoke"
gcloud config set project nomadkaraoke-v2
gcloud services enable firestore.googleapis.com run.googleapis.com \
  cloudfunctionsv2.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com bigquery.googleapis.com \
  cloudtasks.googleapis.com artifactregistry.googleapis.com
```

**2. Set AWS credentials locally** — see § "Setting AWS creds locally" below.

**3. Restore Firestore**
```bash
# Find the latest backup date
LATEST_DATE=$(aws s3 ls s3://nomadkaraoke-backup/firestore/ | awk '{print $2}' | tr -d '/' | sort | tail -1)
echo "Restoring from $LATEST_DATE"

# Create a temporary GCS bucket in the new project to land the import
gsutil mb -l US-CENTRAL1 gs://nomadkaraoke-v2-firestore-import/

aws s3 sync "s3://nomadkaraoke-backup/firestore/${LATEST_DATE}/" /tmp/firestore-restore/
gsutil -m cp -r /tmp/firestore-restore/ gs://nomadkaraoke-v2-firestore-import/

# The Firestore import requires the metadata file emitted by the export — find it
META=$(gsutil ls "gs://nomadkaraoke-v2-firestore-import/firestore-restore/**/all_namespaces*.overall_export_metadata" | head -1)
gcloud firestore import "$META" --project=nomadkaraoke-v2
```

**4. Restore BigQuery**
```bash
# Create dataset
bq mk --dataset nomadkaraoke-v2:karaoke_decide

# Find latest dates
DAILY_DATE=$(aws s3 ls s3://nomadkaraoke-backup/bigquery/daily-refresh/ | awk '{print $2}' | tr -d '/' | sort | tail -1)
MONTHLY_DATE=$(aws s3 ls s3://nomadkaraoke-backup/bigquery/musicbrainz/ | awk '{print $2}' | tr -d '/' | sort | tail -1)

for spec in "daily-refresh|$DAILY_DATE" "musicbrainz|$MONTHLY_DATE"; do
  prefix=${spec%|*}
  date=${spec#*|}
  aws s3 sync "s3://nomadkaraoke-backup/bigquery/$prefix/$date/" "/tmp/bq-$prefix/"
  for dir in /tmp/bq-$prefix/*/; do
    table=$(basename "$dir")
    bq load --source_format=PARQUET nomadkaraoke-v2:karaoke_decide.$table "$dir*.parquet"
  done
done
```

**Spotify tables (Glacier Deep Archive):** Retrieval takes 12-48 hours.
```bash
# Initiate restore (bulk = cheapest)
aws s3api restore-object --bucket nomadkaraoke-backup \
  --key "bigquery/spotify/TABLE/part-000.parquet" \
  --restore-request '{"Days":7,"GlacierJobParameters":{"Tier":"Bulk"}}'
# Wait 12-48 hours, then download and load
```

**5. Restore GCS files**

```bash
# Create new bucket
gsutil mb -l US-CENTRAL1 gs://karaoke-gen-storage-nomadkaraoke-v2/

# Sync from S3 (146 GB last we measured — needs a host with comparable disk + bandwidth)
aws s3 sync s3://nomadkaraoke-backup/gcs/job-files/ /tmp/gcs-restore/
gsutil -m cp -r /tmp/gcs-restore/ gs://karaoke-gen-storage-nomadkaraoke-v2/
```

**6. Restore secrets** — decrypt the latest sealed-box backup and recreate each secret.

```bash
# Get the latest encrypted bundle
LATEST=$(aws s3 ls s3://nomadkaraoke-backup/secrets/ | awk '{print $NF}' | sort | tail -1)
aws s3 cp "s3://nomadkaraoke-backup/secrets/$LATEST" /tmp/secrets.bin

# Install decryption tool if needed
pip install pynacl

# Decrypt — pulls private key from KeepassXC
PRIVATE_KEY_HEX="<paste from KeepassXC 'DR backup decryption key'>"
python3 - <<PY > /tmp/secrets.json
from nacl.public import PrivateKey, SealedBox
import os, json
sk = PrivateKey(bytes.fromhex(os.environ['PRIVATE_KEY_HEX']))
ct = open('/tmp/secrets.bin', 'rb').read()
print(SealedBox(sk).decrypt(ct).decode('utf-8'))
PY

# Recreate every secret in the new project. Review the generated commands first.
python3 - <<'PY' > /tmp/restore_secrets.sh
import json, base64, shlex
bundle = json.load(open('/tmp/secrets.json'))
for name, entry in bundle['secrets'].items():
    if 'error' in entry:
        print(f"# SKIPPED {name}: {entry['error']}", )
        continue
    raw = entry['value']
    if entry['encoding'] == 'base64':
        # Pipe binary through base64 -d to avoid shell-escaping issues
        print(f"echo {shlex.quote(raw)} | base64 -d | gcloud secrets create {name} --data-file=- --project=nomadkaraoke-v2")
    else:
        print(f"printf %s {shlex.quote(raw)} | gcloud secrets create {name} --data-file=- --project=nomadkaraoke-v2")
PY

# Inspect, then execute
less /tmp/restore_secrets.sh
bash /tmp/restore_secrets.sh

# Cleanup
shred -u /tmp/secrets.json /tmp/secrets.bin /tmp/restore_secrets.sh
unset PRIVATE_KEY_HEX
```

**7. Set Pulumi config for new project**
```bash
cd karaoke-gen/infrastructure
pulumi stack init disaster-recovery --copy-config-from prod
pulumi config set gcp:project nomadkaraoke-v2
# The backup encryption pubkey from the prior project still works — its private
# key is in KeepassXC. Either copy it from the old stack or set fresh.
pulumi config set backupEncryptionPubkey <hex>
```

**8. Rebuild infrastructure**
```bash
pulumi up
```

**9. Update CI/CD to target the new project**

GitHub Actions secrets reference `nomadkaraoke` (old project ID). Update them so deploys land in the new project:

```bash
# In the karaoke-gen repo, update these secrets to the new project / new SA key:
gh secret set GCP_PROJECT_ID --body "nomadkaraoke-v2"
gh secret set GCP_SA_KEY --body "$(cat ~/path/to/new-deploy-sa-key.json)"
# (If using Workload Identity Federation, update the audience instead.)
```

Then trigger a deploy or `git push` to redeploy backend + frontend.

**10. Update DNS (Cloudflare)**

Update DNS records to point to new Cloud Run URLs. See `docs/archive/2026-03-29-external-services-config.md`.

**11. Re-register OAuth apps and rotate API keys**

Some external services lock to redirect URIs or treat the old GCP project ID as part of their config. Walk through `docs/archive/2026-03-29-external-services-config.md` end-to-end:

- Spotify Web API + Spotify Connect (redirect URIs)
- Google OAuth (redirect URIs, OAuth consent screen)
- Dropbox app (redirect URIs)
- YouTube OAuth (redirect URIs)
- Stripe webhook endpoint URL (secret regenerates)
- AudioShake API key (rotate as a precaution if old project was compromised)
- Gemini / Vertex API (project-scoped — needs new project enabled)
- Discord webhook URLs (re-issue if old GCP exposure could have leaked them)
- Cloudflare API tokens (rotate)

## Decrypting the secrets backup

```bash
# Set AWS creds (see § "Setting AWS creds locally")
LATEST=$(aws s3 ls s3://nomadkaraoke-backup/secrets/ | awk '{print $NF}' | sort | tail -1)
aws s3 cp "s3://nomadkaraoke-backup/secrets/$LATEST" /tmp/secrets.bin

pip install pynacl
PRIVATE_KEY_HEX="<paste from KeepassXC>"
python3 -c "
import os, json
from nacl.public import PrivateKey, SealedBox
sk = PrivateKey(bytes.fromhex(os.environ['PRIVATE_KEY_HEX']))
bundle = json.loads(SealedBox(sk).decrypt(open('/tmp/secrets.bin','rb').read()))
print(json.dumps({k: '***' for k in bundle['secrets']}, indent=2))
print('Exported at:', bundle['exported_at'])
"
shred -u /tmp/secrets.bin
unset PRIVATE_KEY_HEX
```

## Setting AWS creds locally

The function reads creds from GCP Secret Manager. For local recovery work, retrieve them from KeepassXC (preferred — survives GCP outage) or from Secret Manager (if GCP is up):

```bash
# If GCP is still up:
CREDS=$(gcloud secrets versions access latest --secret=aws-backup-credentials --project=nomadkaraoke)
export AWS_ACCESS_KEY_ID=$(echo "$CREDS" | jq -r .access_key_id)
export AWS_SECRET_ACCESS_KEY=$(echo "$CREDS" | jq -r .secret_access_key)
export AWS_DEFAULT_REGION=$(echo "$CREDS" | jq -r .region)

# If GCP is gone — paste from KeepassXC:
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

## Backup freshness monitor

A GitHub Actions cron (`.github/workflows/dr-backup-freshness.yml`) runs daily at 14:00 UTC and checks that the most recent objects in `s3://nomadkaraoke-backup/firestore/`, `gcs/job-files/`, `secrets/`, and `bigquery/daily-refresh/` are no older than their per-prefix limit. Stale or missing backups → Discord alert + workflow failure.

This monitor runs in GitHub, not GCP, so it survives a complete GCP project loss.

**Required GitHub secrets** (set via `gh secret set`):
- `AWS_BACKUP_READONLY_ACCESS_KEY_ID` — IAM user with `s3:ListBucket` + `s3:GetObject` only on `nomadkaraoke-backup`
- `AWS_BACKUP_READONLY_SECRET_ACCESS_KEY`
- `DR_MONITOR_DISCORD_WEBHOOK` — Discord webhook URL (separate channel from the nightly success/failure alert is fine)

**Manual setup (one-time, in AWS Console):**
1. Create IAM user `nomadkaraoke-backup-monitor` (no console access, programmatic only)
2. Attach inline policy granting `s3:ListBucket` + `s3:GetObject` on `arn:aws:s3:::nomadkaraoke-backup` and `arn:aws:s3:::nomadkaraoke-backup/*`
3. Create access key pair, store in KeepassXC as "Nomad Karaoke — DR backup readonly access"
4. `gh secret set AWS_BACKUP_READONLY_ACCESS_KEY_ID --body '<key>' --repo nomadkaraoke/karaoke-gen`
5. `gh secret set AWS_BACKUP_READONLY_SECRET_ACCESS_KEY --body '<secret>' --repo nomadkaraoke/karaoke-gen`
6. `gh secret set DR_MONITOR_DISCORD_WEBHOOK --body '<url>' --repo nomadkaraoke/karaoke-gen`
7. Trigger a test run: `gh workflow run dr-backup-freshness.yml --repo nomadkaraoke/karaoke-gen`

## Quarterly restore drill

Once per quarter, prove the chain works without committing to a real restore. Scheduler `dr-restore-drill-reminder` posts a Discord nudge each quarter; when it fires, do this:

```bash
# 1. Decrypt yesterday's secrets backup (proves backup + private key + tooling all work)
#    Follow § "Decrypting the secrets backup" above. Do NOT write secrets to disk in plaintext;
#    just confirm the count of decrypted secrets matches gcloud secrets list.
LIVE_COUNT=$(gcloud secrets list --project=nomadkaraoke --format=json | jq length)
echo "Live secret count: $LIVE_COUNT"
# Compare against the count printed by the decryption step.

# 2. Restore Firestore to a side database (does not touch production)
LATEST_DATE=$(aws s3 ls s3://nomadkaraoke-backup/firestore/ | awk '{print $2}' | tr -d '/' | sort | tail -1)
gsutil -m cp -r "s3://nomadkaraoke-backup/firestore/$LATEST_DATE/" gs://nomadkaraoke-backup-staging/drill-import/
META=$(gsutil ls "gs://nomadkaraoke-backup-staging/drill-import/$LATEST_DATE/**/all_namespaces*.overall_export_metadata" | head -1)
gcloud firestore databases create --database=drill-$(date +%Y%m%d) \
  --location=us-central1 --type=firestore-native --project=nomadkaraoke
gcloud firestore import "$META" --database=drill-$(date +%Y%m%d) --project=nomadkaraoke

# 3. Spot-check a few collections in the side database, then delete it
gcloud firestore databases delete drill-$(date +%Y%m%d) --project=nomadkaraoke

# 4. Restore one specific BigQuery table from S3 to a drill_ prefix
bq mk --dataset --description="restore drill" nomadkaraoke:drill_$(date +%Y%m%d)
DAILY_DATE=$(aws s3 ls s3://nomadkaraoke-backup/bigquery/daily-refresh/ | awk '{print $2}' | tr -d '/' | sort | tail -1)
aws s3 cp "s3://nomadkaraoke-backup/bigquery/daily-refresh/$DAILY_DATE/karaokenerds_raw/000000000000.parquet" /tmp/
bq load --source_format=PARQUET nomadkaraoke:drill_$(date +%Y%m%d).karaokenerds_raw /tmp/000000000000.parquet
bq query --use_legacy_sql=false "SELECT COUNT(*) FROM \`nomadkaraoke.drill_$(date +%Y%m%d).karaokenerds_raw\`"
bq rm -r -f -d nomadkaraoke:drill_$(date +%Y%m%d)

# 5. Note the date and result in docs/LESSONS-LEARNED.md
```

## Datasets intentionally NOT in S3 (regenerable from upstream)

These datasets are large and publicly available, so backing them up to S3 would
cost more than re-downloading them after a disaster. If you need them after a
restore, regenerate from these sources:

| Dataset | Source | Re-download notes |
|---|---|---|
| MusicBrainz raw dumps (~17 GB) | https://musicbrainz.org/doc/MusicBrainz_Database/Download | Daily replication available; full import is several hours via `mbslave` or Postgres dump |
| MLHD listening histories (~252 GB) | https://ddmal.music.mcgill.ca/research/The_Music_Listening_Histories_Dataset/ | Static dataset; download once, derived BigQuery tables are in monthly S3 backup |

Only the *derived* BigQuery tables under `bigquery/musicbrainz/` are in S3 (these
contain joins/normalizations that would take hours to recompute even after raw
re-import). The raw source files are not.

## Cost Estimates for Recovery

| Action | Cost |
|--------|------|
| S3 Glacier Deep Archive retrieval (1 TB, bulk) | ~$150 |
| S3 Glacier Deep Archive retrieval (1 TB, standard) | ~$750 |
| S3 data transfer out to GCP | ~$0.09/GB |
| Full Spotify dataset retrieval + transfer (~100 GB) | ~$25 total |
| Full GCS job-files egress (~150 GB) | ~$14 |
