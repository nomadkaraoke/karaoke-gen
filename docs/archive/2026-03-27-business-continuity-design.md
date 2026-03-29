# Business Continuity & Disaster Recovery Design

**Date:** 2026-03-27
**Status:** Design approved, pending implementation
**Budget:** ~$50-100/month recurring (actual estimate: $20-40/month) + ~$900 one-time egress for Phase 3
**RPO:** 24 hours for Tier 1 (critical), 1 week for Tier 2 (high), N/A for Tier 3 (one-time)
**RTO:** Days (acceptable downtime while rebuilding from git/Pulumi + restoring data from backups)

## Risk Model

Ranked by priority:

1. **Errant Claude session** — AI agent with admin gcloud credentials accidentally deletes production data
2. **Google account termination** — GCP project suspended due to ToS concerns (Spotify dataset, YouTube cookies, torrent tracker integration)
3. **Accidental human error** — Destructive gcloud/bq command run by mistake

## Strategy: Two-Tier Protection

- **Tier A (GCP-native):** Fast recovery from mistakes — object versioning, PITR, deny policies, read-only SA
- **Tier B (Cross-cloud):** Catastrophic recovery — nightly exports to AWS S3 in a separate account

---

## 1. Data Classification

### Tier 1 — Critical (irreplaceable user-generated data)

| Data | Location | Size | Backup Freq | S3 Destination |
|------|----------|------|-------------|----------------|
| Firestore (all collections) | `nomadkaraoke` default DB | ~MBs | Nightly | Standard-IA |
| GCS job files | `karaoke-gen-storage-nomadkaraoke` | 249 GB | Nightly delta sync | Standard-IA |
| Secrets Manager | 48 secrets | Tiny | On-change + quarterly | AWS Secrets Manager |
| Stripe config | External service | N/A | Documented | AWS Secrets Manager |

**Firestore collections (gen):** `gen_users`, `jobs`, `jobs/{id}/logs`, `sessions`, `magic_links`, `user_feedback`, `beta_feedback`, `youtube_quota_pending`, `youtube_upload_queue`

**Firestore collections (decide):** `decide_users`, `lastfm_users`, `music_services`, `karaoke_songs`, `user_songs`, `user_artists`, `playlists`, `sync_jobs`, `sung_records`

### Tier 2 — High (expensive to recreate)

| Data | Location | Size | Backup Freq | S3 Destination |
|------|----------|------|-------------|----------------|
| BigQuery daily-refresh tables | `karaokenerds_raw`, `karaokenerds_community`, `divebar_catalog`, `kn_divebar_xref` | ~MBs | Weekly | Standard-IA |
| BigQuery MusicBrainz tables | `mb_artists`, `mb_recordings`, `mb_artist_tags`, `mb_recording_isrc`, `mbid_spotify_mapping`, `mb_artists_normalized`, `karaoke_recording_links` | ~5 GB | Monthly | Standard-IA |
| BigQuery MLHD+ | `mlhd_artist_similarity` | ~50 MB | One-time | Standard-IA |
| GCS MusicBrainz data | `nomadkaraoke-musicbrainz-data` | 17 GB | One-time | Glacier |
| GCS MLHD data | `nomadkaraoke-mlhd-data` | 252 GB | One-time | Glacier |
| GCS KN data | `nomadkaraoke-kn-data` | 44 MB | Weekly | Standard-IA |

### Tier 3 — Replaceable (can re-derive)

| Data | Location | Size | Backup Freq | S3 Destination |
|------|----------|------|-------------|----------------|
| BigQuery Spotify tables (13 tables) | `karaoke_decide` dataset | ~1 TB logical | One-time | Glacier Deep Archive |
| GCS raw archives | `nomadkaraoke-raw-archives` | 7.2 TB | One-time | Glacier Deep Archive |

### Not Backed Up

| Data | Reason |
|------|--------|
| GCS `nomad-usb-backup` (593 GB) | Already a backup itself |
| Artifact Registry (390 GB) | Rebuild from git |
| Cloud Run services | Code in GitHub, deploy via Pulumi |
| Cloud Scheduler / Cloud Tasks | Defined in Pulumi IaC |
| GCE VMs | Pulumi + Packer, recreatable |
| Firestore indexes | Defined in Pulumi |
| IAM / Service accounts | Defined in Pulumi |
| GCS `nomadkaraoke-audio-separator-models` | Empty bucket |
| GCS `karaoke-decide-data` | Empty bucket |

---

## 2. Prevention Layer: Read-Only Claude SA

### Service Account: `claude-readonly@nomadkaraoke.iam.gserviceaccount.com`

**Granted roles:**

| Role | Purpose |
|------|---------|
| `roles/viewer` | Project-wide read access |
| `roles/firestore.viewer` | Read Firestore documents |
| `roles/bigquery.dataViewer` | Query BigQuery tables |
| `roles/bigquery.jobUser` | Execute BigQuery queries |
| `roles/storage.objectViewer` | Read GCS objects |
| `roles/secretmanager.viewer` | List secrets (NOT access values) |
| `roles/run.viewer` | View Cloud Run services/logs |
| `roles/compute.viewer` | View GCE instances |
| `roles/logging.viewer` | Read Cloud Logging |

### Usage Pattern

- Default: `GOOGLE_APPLICATION_CREDENTIALS` points to read-only SA key
- Write access: User explicitly switches to admin credentials for specific tasks
- Alternative: Claude asks user to run write commands via `!` prefix

### IAM Deny Policies (apply to ALL principals except break-glass SA)

Protect against catastrophic deletes even with admin credentials:

```yaml
deniedPermissions:
  - firestore.googleapis.com/databases.delete
  - storage.googleapis.com/buckets.delete
  - bigquery.googleapis.com/datasets.delete
  - bigquery.googleapis.com/tables.delete  # scoped to karaoke_decide dataset only
exceptionPrincipals:
  - serviceAccount:break-glass@nomadkaraoke.iam.gserviceaccount.com
```

The `break-glass` SA key is stored offline (password manager / local encrypted file). For legitimate deletes, use the break-glass SA rather than removing the deny policy itself.

**Note:** `bigquery.tables.delete` is scoped to the `karaoke_decide` dataset only (not project-wide) to avoid blocking temp table cleanup by the backup pipeline or other automation.

---

## 3. GCP-Native Protections

| Protection | Resource | Config | Cost |
|------------|----------|--------|------|
| Object Versioning | `karaoke-gen-storage-nomadkaraoke` | Enable + lifecycle: delete old versions after 30 days | ~$5-10/month |
| Firestore PITR | Default database | Enable 7-day Point-in-Time Recovery | Free |
| BigQuery Time Travel | `karaoke_decide` dataset | Already enabled (7-day window) | Free |
| GCS Soft Delete | All critical buckets | 7-day soft delete retention | Minimal |

---

## 4. AWS Backup Infrastructure

### Account Setup

- New AWS account: dedicated to Nomad Karaoke backups
- Region: `us-east-1`
- IAM user for backup pipeline: write-only S3 access (no delete)

### S3 Bucket Structure

```
s3://nomadkaraoke-backup/
├── firestore/
│   └── YYYY-MM-DD/              # Nightly Firestore exports
├── bigquery/
│   ├── daily-refresh/            # KN, Divebar tables (weekly)
│   ├── musicbrainz/              # Monthly
│   ├── spotify/                  # One-time, Glacier Deep Archive
│   └── mlhd/                    # One-time
├── gcs/
│   ├── job-files/                # Mirror of karaoke-gen-storage
│   ├── kn-data/                  # Weekly
│   └── musicbrainz-data/        # One-time
└── secrets/
    └── YYYY-MM-DD.enc           # Encrypted secrets snapshot
```

### S3 Lifecycle Policies

| Prefix | Storage Class | Retention |
|--------|---------------|-----------|
| `firestore/` | Standard-IA | Keep last 30 daily, delete older |
| `bigquery/daily-refresh/` | Standard-IA | Keep last 8 weekly |
| `bigquery/spotify/` | Glacier Deep Archive | Indefinite |
| `gcs/job-files/` | Standard-IA | Mirror (sync, no retention limit) |
| `secrets/` | Standard | Keep last 4 quarterly snapshots |

### Backup Pipeline

**Cloud Function `backup-to-aws`** (2nd gen, Python, 60-min timeout) triggered by Cloud Scheduler nightly at 1:00 AM ET.

Uses Python client libraries (NOT CLI tools — `gcloud`/`bq`/`gsutil` are not available in Cloud Functions):

1. **Firestore export** → `google.cloud.firestore_admin_v1` client to export to `gs://nomadkaraoke-backup-staging/firestore/YYYY-MM-DD/`
2. **BigQuery export** (weekly, based on day-of-week check) → `google.cloud.bigquery` client `extract_table()` to GCS staging as Parquet
3. **GCS sync** → `google.cloud.storage` client to copy new/changed job files to staging bucket
4. **AWS sync** → `boto3` to upload from GCS staging to S3
5. **Cleanup** → Remove GCS staging files after confirmed S3 upload
6. **Alert** → Discord webhook notification with backup summary + any failures

**Required IAM roles for backup function SA (`backup-to-aws@nomadkaraoke.iam.gserviceaccount.com`):**
- `roles/datastore.importExportAdmin` — Firestore export
- `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` — BigQuery export
- `roles/storage.objectAdmin` on staging bucket — Write exports
- `roles/storage.objectViewer` on source buckets — Read job files

**Note:** For the 249 GB job-files bucket, delta sync may exceed Cloud Function timeout on first run. If needed, split into a separate Cloud Run job for the initial catchup, then use the Cloud Function for incremental deltas.

### Cost Estimate

**Recurring (monthly):**

| Component | Monthly Cost |
|-----------|-------------|
| S3 Glacier Deep Archive (7.5 TB) | ~$7 |
| S3 Standard-IA (~300 GB active) | ~$4 |
| GCP egress for nightly deltas | ~$5-15 |
| Cloud Function execution | ~$1 |
| GCS staging bucket | ~$2 |
| **Total recurring** | **~$20-40** |

**One-time (Phase 3 bulk transfer):**

| Component | Cost |
|-----------|------|
| GCP egress for ~7.5 TB to AWS | ~$900 |
| S3 Glacier Deep Archive retrieval (if ever needed) | ~$150 (bulk) to ~$750 (standard) |

---

## 5. Additional Single Points of Failure

Beyond GCP, these external dependencies should be documented/protected:

| Dependency | Risk | Mitigation |
|------------|------|------------|
| **GitHub** (all repos, IaC, CI/CD) | Org suspended/deleted | Keep local clones of all repos; consider periodic mirror to AWS CodeCommit |
| **Cloudflare** (DNS, Pages, Workers) | Account compromised/terminated | Export DNS zone records, document Pages/Worker config in this repo |
| **External OAuth apps** (Spotify, Google, Dropbox) | Losing Google account = losing OAuth app registrations | Document all OAuth redirect URIs, client IDs, app settings in encrypted backup |
| **Flacfetch VM data disk** | Chrome profile, cookies, tracker config | Document manual setup steps; cookies auto-refresh so only config matters |

## 6. Secrets Encryption & Key Management

Secrets backup files (`secrets/YYYY-MM-DD.enc`) are encrypted with an **AWS KMS key** (not GCP KMS — must survive GCP termination).

- AWS KMS key created in the backup AWS account
- Key ARN also stored in password manager as a failsafe
- The backup Cloud Function encrypts secrets client-side using `boto3` KMS before uploading to S3

**Critical:** AWS IAM credentials for the backup pipeline are stored in GCP Secret Manager for the Cloud Function to use, BUT must also be stored **outside GCP** (password manager) since they are needed to access backups if GCP is terminated.

## 7. Verification & Monitoring

| Check | Frequency | Method |
|-------|-----------|--------|
| Backup completed | Daily | Discord webhook alert from backup function |
| Backup freshness (external) | Daily | External cron (e.g., free-tier UptimeRobot or AWS Lambda) checks S3 last-modified; alerts if >48h stale |
| Backup integrity | Monthly | Script downloads random Firestore export, verifies doc count |
| S3 bucket growth | Weekly | CloudWatch alarm if bucket stops growing |
| Restore drill | Quarterly | Restore Firestore to test GCP project, verify data |

**Why external monitoring?** The backup pipeline runs on GCP. If GCP is terminated (Risk #2), backups stop silently. The external freshness check ensures you are alerted even if GCP is down.

## 8. High-Level Restore Procedure

For a catastrophic GCP termination, the restore flow would be:

1. **Create new GCP project** (or use a different cloud provider)
2. **Retrieve AWS credentials** from password manager
3. **Download Firestore export** from S3 → `firestore import` to new project
4. **Download BigQuery exports** from S3 → load Parquet files into new BigQuery (or equivalent)
5. **Sync GCS data** from S3 → new cloud storage
6. **Retrieve secrets** from AWS Secrets Manager → decrypt with AWS KMS → configure new project
7. **Run Pulumi** targeting new project to recreate all infrastructure
8. **Deploy services** from GitHub repos
9. **Update DNS** (Cloudflare) to point to new services
10. **Re-register OAuth apps** at Spotify, Google, etc. using documented config

---

## 9. Implementation Phases

### Phase 0: Audit (verify assumptions)
1. Run `pulumi stack export` and diff against actual GCP resources to catch any console-created resources not in IaC
2. Verify all Cloud Scheduler jobs are defined in Pulumi
3. Document Cloudflare configuration (DNS records, Pages projects, Worker scripts, custom domains)
4. Document external OAuth app registrations (Spotify, Google, Dropbox redirect URIs and client IDs)

### Phase 1: GCP-Native Protections (no AWS needed)
1. Create `claude-readonly` service account via Pulumi
2. Grant read-only roles
3. Create `break-glass` service account, store key in password manager
4. Create IAM deny policies for destructive operations (with break-glass exception)
5. Enable Firestore PITR
6. Enable GCS object versioning on `karaoke-gen-storage-nomadkaraoke`
7. Enable GCS soft delete on critical buckets
8. Generate `claude-readonly` SA key, store at `~/.config/gcloud/claude-readonly.json`
9. Configure direnv (`.envrc`) to set `GOOGLE_APPLICATION_CREDENTIALS` to read-only SA by default

### Phase 2: AWS Account & Bucket Setup
1. Create dedicated AWS account
2. Create S3 bucket with lifecycle policies
3. Enable S3 Object Lock (Governance mode) on backup bucket
4. Create write-only IAM user (`s3:PutObject` only, no delete)
5. Create AWS KMS key for secrets encryption
6. Create AWS Secrets Manager entries for external service config
7. Store AWS credentials in GCP Secret Manager (for backup function) AND in password manager (for catastrophic recovery)

### Phase 3: One-Time Bulk Exports (~$900 egress cost)
1. Export all BigQuery Spotify tables to GCS staging
2. Use GCP Storage Transfer Service (direct GCS→S3, no staging needed) for 7.2 TB raw archives to Glacier Deep Archive
3. Sync MusicBrainz and MLHD GCS buckets to S3 Glacier
4. Export and sync MLHD+ BigQuery table
5. Export all secrets, encrypt with AWS KMS, store in AWS Secrets Manager
6. Store KMS key ARN in password manager

### Phase 4: Automated Nightly Backups
1. Create GCS staging bucket (`nomadkaraoke-backup-staging`)
2. Create backup function SA with required roles (`datastore.importExportAdmin`, `bigquery.dataViewer`, `bigquery.jobUser`, `storage.objectAdmin` on staging, `storage.objectViewer` on sources)
3. Create 2nd-gen Cloud Function `backup-to-aws` (Python, 60-min timeout)
4. Create Cloud Scheduler trigger (1:00 AM ET daily)
5. Implement Firestore nightly export (via `firestore_admin_v1` Python client)
6. Implement BigQuery weekly export (via `bigquery` Python client, day-of-week check)
7. Implement BigQuery monthly export for MusicBrainz tables
8. Implement GCS job files delta sync (via `google.cloud.storage` + `boto3`)
9. Implement Discord webhook alerting on success/failure
10. Set up external backup freshness monitor (AWS Lambda or free-tier service, alerts if S3 not updated in 48h)

### Phase 5: Verification & Documentation
1. Run first full backup, verify all data landed in S3
2. Test Firestore restore to a test GCP project
3. Write detailed restore runbook (step-by-step for each data tier, including cost estimates for Glacier retrieval)
4. Set up CloudWatch monitoring on S3
5. Mirror GitHub repos to secondary git host (AWS CodeCommit or similar)
6. Schedule first quarterly restore drill
