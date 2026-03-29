# Business Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement two-tier DR: GCP-native protections (fast recovery) + cross-cloud AWS backups (catastrophic recovery), with a read-only Claude SA and IAM deny policies for prevention.

**Architecture:** Pulumi IaC manages all GCP resources (new SA, deny policies, PITR, versioning, backup Cloud Function, staging bucket). A nightly 2nd-gen Cloud Function exports Firestore/BigQuery/GCS to a staging bucket, then pushes to AWS S3 via boto3. AWS account/bucket/IAM are set up manually (outside IaC).

**Tech Stack:** Pulumi (Python), GCP Cloud Functions v2, Firestore Admin API, BigQuery Python client, boto3, AWS S3/KMS/Secrets Manager

**Spec:** `docs/archive/2026-03-27-business-continuity-design.md`

---

## File Structure

### Infrastructure (Pulumi)

| File | Responsibility |
|------|---------------|
| Create: `infrastructure/modules/iam/claude_readonly_sa.py` | Read-only SA + break-glass SA + IAM deny policies |
| Create: `infrastructure/modules/backup.py` | Backup infrastructure: staging bucket, backup SA, Cloud Function, Cloud Scheduler |
| Modify: `infrastructure/modules/database.py` | Enable Firestore PITR |
| Modify: `infrastructure/modules/storage.py` | Enable GCS versioning + soft delete |
| Modify: `infrastructure/__main__.py` | Wire new modules |

### Backup Cloud Function

| File | Responsibility |
|------|---------------|
| Create: `infrastructure/functions/backup_to_aws/main.py` | Cloud Function entry point — orchestrates all backup steps |
| Create: `infrastructure/functions/backup_to_aws/firestore_export.py` | Firestore export via Admin API |
| Create: `infrastructure/functions/backup_to_aws/bigquery_export.py` | BigQuery table extraction to GCS Parquet |
| Create: `infrastructure/functions/backup_to_aws/gcs_sync.py` | GCS delta sync (new/changed files) |
| Create: `infrastructure/functions/backup_to_aws/s3_upload.py` | Upload from GCS staging to S3 via boto3 |
| Create: `infrastructure/functions/backup_to_aws/discord_alert.py` | Discord webhook notifications |
| Create: `infrastructure/functions/backup_to_aws/requirements.txt` | Python dependencies |

### Tests

| File | Responsibility |
|------|---------------|
| Create: `infrastructure/functions/backup_to_aws/test_main.py` | Unit tests for orchestration logic |
| Create: `infrastructure/functions/backup_to_aws/test_firestore_export.py` | Unit tests for Firestore export |
| Create: `infrastructure/functions/backup_to_aws/test_bigquery_export.py` | Unit tests for BigQuery export |

### Documentation

| File | Responsibility |
|------|---------------|
| Create: `docs/DISASTER-RECOVERY.md` | Restore runbook — step-by-step for each data tier |
| Create: `docs/archive/2026-03-29-external-services-config.md` | Documented OAuth redirect URIs, Cloudflare config, etc. |

---

## Task 1: Enable GCP-Native Protections (Pulumi)

**Files:**
- Modify: `infrastructure/modules/database.py`
- Modify: `infrastructure/modules/storage.py`
- Modify: `infrastructure/__main__.py`

- [ ] **Step 1: Enable Firestore PITR**

In `infrastructure/modules/database.py`, add `point_in_time_recovery_enablement` to the Firestore database resource:

```python
# In create_database(), modify the firestore.Database resource:
firestore_db = firestore.Database(
    "karaoke-firestore",
    project=PROJECT_ID,
    name="(default)",
    location_id="us-central1",
    type="FIRESTORE_NATIVE",
    concurrency_mode="PESSIMISTIC",
    point_in_time_recovery_enablement="POINT_IN_TIME_RECOVERY_ENABLED",
    # ... rest of existing config
)
```

- [ ] **Step 2: Enable GCS versioning and soft delete on main bucket**

In `infrastructure/modules/storage.py`, add versioning and soft_delete to `create_bucket()`:

```python
bucket = storage.Bucket(
    "karaoke-storage",
    name=f"karaoke-gen-storage-{PROJECT_ID}",
    location="US-CENTRAL1",
    force_destroy=False,
    uniform_bucket_level_access=True,
    versioning=storage.BucketVersioningArgs(enabled=True),
    soft_delete_policy=storage.BucketSoftDeletePolicyArgs(
        retention_duration_seconds=604800,  # 7 days
    ),
    cors=[...],  # existing
    lifecycle_rules=[
        # existing rules...
        # Add: delete non-current versions after 30 days
        storage.BucketLifecycleRuleArgs(
            action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
            condition=storage.BucketLifecycleRuleConditionArgs(
                num_newer_versions=1,
                days_since_noncurrent_time=30,
            ),
        ),
    ],
)
```

- [ ] **Step 3: Run `pulumi preview` to verify changes**

```bash
cd infrastructure && pulumi preview --diff 2>&1 | head -100
```

Expected: Shows updates to Firestore database (PITR) and GCS bucket (versioning, soft delete, lifecycle rule). No deletes.

- [ ] **Step 4: Run `pulumi up` to apply**

```bash
pulumi up --yes
```

**⚠️ User must confirm:** This modifies production Firestore and GCS. Run locally before merging.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/modules/database.py infrastructure/modules/storage.py
git commit -m "feat: enable Firestore PITR and GCS versioning for DR"
```

---

## Task 2: Create Read-Only and Break-Glass Service Accounts

**Files:**
- Create: `infrastructure/modules/iam/claude_readonly_sa.py`
- Modify: `infrastructure/__main__.py`

**Note on existing `claude-automation` SA:** The `claude-automation` SA already exists with limited read-only access. The new `claude-readonly` SA has broader read permissions (Firestore, BigQuery, Secrets listing). After deployment, `claude-readonly` becomes the default for Claude sessions. The existing `claude-automation` SA can remain for backwards compatibility or be deprecated in a future cleanup.

- [ ] **Step 1: Create the claude-readonly SA module**

Create `infrastructure/modules/iam/claude_readonly_sa.py`:

```python
"""
Claude read-only service account and break-glass SA for business continuity.

The claude-readonly SA provides safe default credentials for Claude Code sessions.
The break-glass SA is excepted from deny policies for emergency operations.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import serviceaccount

from config import PROJECT_ID


def create_claude_readonly_service_account() -> serviceaccount.Account:
    """Create read-only service account for Claude Code sessions."""
    return serviceaccount.Account(
        "claude-readonly-sa",
        account_id="claude-readonly",
        display_name="Claude Code Read-Only",
        description="Default read-only SA for Claude Code sessions — no write access to production",
    )


def create_break_glass_service_account() -> serviceaccount.Account:
    """Create break-glass SA excepted from IAM deny policies."""
    return serviceaccount.Account(
        "break-glass-sa",
        account_id="break-glass",
        display_name="Break Glass Emergency Access",
        description="Emergency SA excepted from deny policies — key stored offline only",
    )


def grant_claude_readonly_permissions(
    service_account: serviceaccount.Account,
) -> dict:
    """Grant read-only permissions across the project."""
    bindings = {}
    roles = {
        "viewer": "roles/viewer",
        "firestore_viewer": "roles/datastore.viewer",
        "bigquery_data_viewer": "roles/bigquery.dataViewer",
        "bigquery_job_user": "roles/bigquery.jobUser",
        "storage_viewer": "roles/storage.objectViewer",
        "secretmanager_viewer": "roles/secretmanager.viewer",
        "run_viewer": "roles/run.viewer",
        "compute_viewer": "roles/compute.viewer",
        "logging_viewer": "roles/logging.viewer",
    }

    for key, role in roles.items():
        bindings[key] = gcp.projects.IAMMember(
            f"claude-readonly-{key.replace('_', '-')}",
            project=PROJECT_ID,
            role=role,
            member=service_account.email.apply(
                lambda email: f"serviceAccount:{email}"
            ),
        )

    return bindings


def grant_impersonation_permission(
    service_account: serviceaccount.Account,
) -> serviceaccount.IAMBinding:
    """Allow users to impersonate the read-only SA."""
    return serviceaccount.IAMBinding(
        "claude-readonly-impersonation",
        service_account_id=service_account.name,
        role="roles/iam.serviceAccountTokenCreator",
        members=[
            "user:andrew@beveridge.uk",
            "user:admin@nomadkaraoke.com",
        ],
    )
```

- [ ] **Step 2: Wire into `__main__.py`**

Add after the existing `claude_automation_sa` section:

```python
from modules.iam import claude_readonly_sa

# Claude read-only service account (default for Claude Code sessions)
claude_readonly_service_account = claude_readonly_sa.create_claude_readonly_service_account()
claude_readonly_iam_bindings = claude_readonly_sa.grant_claude_readonly_permissions(
    claude_readonly_service_account
)
claude_readonly_impersonation = claude_readonly_sa.grant_impersonation_permission(
    claude_readonly_service_account
)

# Break-glass service account (excepted from deny policies)
break_glass_service_account = claude_readonly_sa.create_break_glass_service_account()

# Exports
pulumi.export("claude_readonly_service_account", claude_readonly_service_account.email)
pulumi.export("break_glass_service_account", break_glass_service_account.email)
```

- [ ] **Step 3: Run `pulumi preview`**

```bash
cd infrastructure && pulumi preview --diff 2>&1 | head -60
```

Expected: Creates 2 service accounts + ~10 IAM bindings + 1 impersonation binding. No updates/deletes.

- [ ] **Step 4: Run `pulumi up`**

```bash
pulumi up --yes
```

- [ ] **Step 5: Commit**

```bash
git add infrastructure/modules/iam/claude_readonly_sa.py infrastructure/__main__.py
git commit -m "feat: create claude-readonly and break-glass service accounts"
```

---

## Task 3: Create IAM Deny Policies

**Files:**
- Modify: `infrastructure/modules/iam/claude_readonly_sa.py`
- Modify: `infrastructure/__main__.py`

- [ ] **Step 1: Add deny policy function to the SA module**

Append to `infrastructure/modules/iam/claude_readonly_sa.py`:

```python
def create_deny_policies(
    break_glass_sa: serviceaccount.Account,
) -> dict:
    """Create IAM deny policies to prevent catastrophic deletes.

    The break-glass SA is excepted from all deny policies.
    Uses project NUMBER (not ID) for principal URIs as required by IAM v2.
    """
    from config import get_project_number

    policies = {}
    project_number = get_project_number()

    # Deny destructive operations project-wide
    policies["deny_destructive_ops"] = gcp.iam.DenyPolicy(
        "deny-destructive-operations",
        parent=f"cloudresourcemanager.googleapis.com/projects/{PROJECT_ID}",
        name="deny-destructive-operations",
        rules=[
            gcp.iam.DenyPolicyRuleArgs(
                deny_rule=gcp.iam.DenyPolicyRuleDenyRuleArgs(
                    denied_principals=["principalSet://goog/public:all"],
                    denied_permissions=[
                        "firestore.googleapis.com/databases.delete",
                        "storage.googleapis.com/buckets.delete",
                        "bigquery.googleapis.com/datasets.delete",
                    ],
                    exception_principals=[
                        break_glass_sa.email.apply(
                            lambda email: f"principal://iam.googleapis.com/projects/{project_number}/serviceAccounts/{email}"
                        ),
                    ],
                ),
            ),
        ],
    )

    return policies
```

**Note:** BigQuery `tables.delete` deny is intentionally omitted from Pulumi. The project-wide deny on `datasets.delete` already prevents the most catastrophic scenario. A dataset-scoped table delete deny would require a dataset-level IAM policy which adds complexity — we rely on BigQuery time-travel (7 days) for table-level recovery instead.

- [ ] **Step 2: Wire deny policies into `__main__.py`**

Add after the break-glass SA creation:

```python
# IAM deny policies (prevent catastrophic deletes for all principals)
deny_policies = claude_readonly_sa.create_deny_policies(break_glass_service_account)
```

- [ ] **Step 3: Run `pulumi preview`**

```bash
cd infrastructure && pulumi preview --diff 2>&1 | head -40
```

Expected: Creates 1 deny policy resource.

- [ ] **Step 4: Run `pulumi up`**

```bash
pulumi up --yes
```

- [ ] **Step 5: Verify deny policy is active**

```bash
gcloud iam policies list --project=nomadkaraoke --kind=denypolicies 2>&1
```

Expected: Shows `deny-destructive-operations` policy.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/modules/iam/claude_readonly_sa.py infrastructure/__main__.py
git commit -m "feat: add IAM deny policies to prevent catastrophic deletes"
```

---

## Task 4: Generate SA Key and Configure Local Environment

**Files:** None (manual steps only)

**⚠️ This task requires human action — Claude cannot create keys or edit password managers.**

- [ ] **Step 1: Generate claude-readonly SA key**

```bash
gcloud iam service-accounts keys create ~/.config/gcloud/claude-readonly.json \
  --iam-account=claude-readonly@nomadkaraoke.iam.gserviceaccount.com \
  --project=nomadkaraoke
```

- [ ] **Step 2: Generate break-glass SA key and store offline**

```bash
gcloud iam service-accounts keys create /tmp/break-glass-key.json \
  --iam-account=break-glass@nomadkaraoke.iam.gserviceaccount.com \
  --project=nomadkaraoke
```

Then: Copy contents to password manager, delete the local file:
```bash
cat /tmp/break-glass-key.json  # copy to password manager
rm /tmp/break-glass-key.json
```

- [ ] **Step 3: Configure direnv for nomadkaraoke workspace**

Add to `/Users/andrew/Projects/nomadkaraoke/.envrc`:

```bash
# Default to read-only GCP credentials for Claude Code sessions
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/gcloud/claude-readonly.json"
```

Then:
```bash
direnv allow /Users/andrew/Projects/nomadkaraoke
```

- [ ] **Step 4: Verify read-only access works**

```bash
# Should work (read)
gcloud firestore documents list projects/nomadkaraoke/databases/\(default\)/documents/gen_users --limit=1

# Should fail (write) — this confirms the SA is read-only
# (Don't actually run this, just confirming the principle)
```

---

## Task 5: AWS Account and Bucket Setup

**⚠️ This task is mostly manual — AWS Console + CLI.**

- [ ] **Step 1: Create dedicated AWS account**

Create new AWS account at https://aws.amazon.com. Suggested name: `nomadkaraoke-backup`.

- [ ] **Step 2: Install and configure AWS CLI**

```bash
brew install awscli  # if not installed
aws configure --profile nomadkaraoke-backup
# Enter: Access Key ID, Secret Key, Region: us-east-1, Output: json
```

- [ ] **Step 3: Create S3 bucket with Object Lock**

```bash
aws s3api create-bucket \
  --bucket nomadkaraoke-backup \
  --region us-east-1 \
  --object-lock-enabled-for-bucket \
  --profile nomadkaraoke-backup
```

- [ ] **Step 4: Configure S3 lifecycle policies**

Create `/tmp/s3-lifecycle.json`:

```json
{
  "Rules": [
    {
      "ID": "firestore-retention-30d",
      "Filter": {"Prefix": "firestore/"},
      "Status": "Enabled",
      "Expiration": {"Days": 30},
      "Transitions": [{"Days": 7, "StorageClass": "STANDARD_IA"}]
    },
    {
      "ID": "bigquery-weekly-retention-60d",
      "Filter": {"Prefix": "bigquery/daily-refresh/"},
      "Status": "Enabled",
      "Expiration": {"Days": 60},
      "Transitions": [{"Days": 7, "StorageClass": "STANDARD_IA"}]
    },
    {
      "ID": "bigquery-spotify-glacier",
      "Filter": {"Prefix": "bigquery/spotify/"},
      "Status": "Enabled",
      "Transitions": [{"Days": 1, "StorageClass": "DEEP_ARCHIVE"}]
    },
    {
      "ID": "gcs-job-files-ia",
      "Filter": {"Prefix": "gcs/job-files/"},
      "Status": "Enabled",
      "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}]
    }
  ]
}
```

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket nomadkaraoke-backup \
  --lifecycle-configuration file:///tmp/s3-lifecycle.json \
  --profile nomadkaraoke-backup
```

- [ ] **Step 5: Create write-only IAM user**

```bash
# Create user
aws iam create-user --user-name backup-writer --profile nomadkaraoke-backup

# Create policy (PutObject only, no delete)
aws iam create-policy --policy-name S3BackupWriteOnly --policy-document '{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::nomadkaraoke-backup", "arn:aws:s3:::nomadkaraoke-backup/*"]
    }
  ]
}' --profile nomadkaraoke-backup

# Attach policy
aws iam attach-user-policy --user-name backup-writer \
  --policy-arn arn:aws:iam::ACCOUNT_ID:policy/S3BackupWriteOnly \
  --profile nomadkaraoke-backup

# Create access key
aws iam create-access-key --user-name backup-writer --profile nomadkaraoke-backup
# Save the AccessKeyId and SecretAccessKey
```

- [ ] **Step 6: Create AWS KMS key for secrets encryption**

```bash
aws kms create-key --description "Nomad Karaoke backup secrets encryption" \
  --profile nomadkaraoke-backup
# Note the KeyId — store in password manager
```

- [ ] **Step 7: Store AWS credentials in GCP Secret Manager**

```bash
# Store as JSON: {"access_key_id": "...", "secret_access_key": "...", "region": "us-east-1"}
echo -n '{"access_key_id":"AKIA...","secret_access_key":"...","region":"us-east-1"}' | \
  gcloud secrets create aws-backup-credentials --data-file=- --project=nomadkaraoke
```

- [ ] **Step 8: Store AWS credentials in password manager**

Store the same credentials (access key, secret key, KMS key ARN, S3 bucket name) in password manager as a failsafe — these are needed if GCP is terminated.

---

## Task 6: Create Backup Cloud Function — Core Module

**Files:**
- Create: `infrastructure/functions/backup_to_aws/requirements.txt`
- Create: `infrastructure/functions/backup_to_aws/main.py`
- Create: `infrastructure/functions/backup_to_aws/discord_alert.py`

- [ ] **Step 1: Create requirements.txt**

```
functions-framework==3.*
google-cloud-firestore-admin==1.*
google-cloud-bigquery==3.*
google-cloud-storage==2.*
google-cloud-secret-manager==2.*
boto3==1.*
requests==2.*
```

- [ ] **Step 2: Create discord_alert.py**

```python
"""Discord webhook notifications for backup status."""

import json
import logging
import requests

logger = logging.getLogger(__name__)


def send_alert(webhook_url: str, title: str, fields: list[dict], success: bool = True):
    """Send a Discord embed notification.

    Args:
        webhook_url: Discord webhook URL.
        title: Embed title.
        fields: List of {"name": ..., "value": ..., "inline": bool} dicts.
        success: True for green, False for red embed color.
    """
    color = 0x00FF00 if success else 0xFF0000
    embed = {
        "title": title,
        "color": color,
        "fields": fields,
    }
    payload = {"embeds": [embed]}

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Discord alert: {e}")
```

- [ ] **Step 3: Create main.py — orchestrator**

```python
"""
Backup to AWS Cloud Function.

Nightly backup pipeline:
1. Firestore export to GCS staging
2. BigQuery export to GCS staging (weekly/monthly schedule)
3. GCS job files delta sync to staging
4. Upload staging files to S3
5. Cleanup staging
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
from s3_upload import upload_staging_to_s3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "nomadkaraoke-backup-staging")
S3_BUCKET = os.environ.get("S3_BUCKET", "nomadkaraoke-backup")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "nomadkaraoke")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


@functions_framework.http
def backup_to_aws(request):
    """Main entry point for the backup Cloud Function."""
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
            day_of_week=today.weekday(),  # 6 = Sunday
            day_of_month=today.day,
        )
        results["bigquery"] = bq_results
    except Exception as e:
        logger.error(f"BigQuery export failed: {e}")
        errors.append(f"BigQuery: {e}")

    # Step 3: GCS delta sync (nightly)
    try:
        results["gcs_sync"] = sync_gcs_to_staging(
            source_bucket="karaoke-gen-storage-nomadkaraoke",
            staging_bucket=STAGING_BUCKET,
            staging_prefix=f"gcs/job-files/",
        )
    except Exception as e:
        logger.error(f"GCS sync failed: {e}")
        errors.append(f"GCS sync: {e}")

    # Step 4: Upload to S3
    try:
        results["s3_upload"] = upload_staging_to_s3(
            staging_bucket=STAGING_BUCKET,
            s3_bucket=S3_BUCKET,
        )
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        errors.append(f"S3 upload: {e}")

    # Step 5: Discord alert
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
```

- [ ] **Step 4: Commit**

```bash
git add infrastructure/functions/backup_to_aws/
git commit -m "feat: add backup-to-aws Cloud Function — main orchestrator and discord alerts"
```

---

## Task 7: Backup Cloud Function — Firestore Export

**Files:**
- Create: `infrastructure/functions/backup_to_aws/firestore_export.py`
- Create: `infrastructure/functions/backup_to_aws/test_firestore_export.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for firestore_export module."""

import unittest
from unittest.mock import MagicMock, patch


class TestFirestoreExport(unittest.TestCase):
    @patch("firestore_export.firestore_admin_v1.FirestoreAdminClient")
    def test_export_firestore_calls_api(self, mock_client_class):
        from firestore_export import export_firestore

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock()
        mock_client.export_documents.return_value = mock_operation

        result = export_firestore(
            project="test-project",
            staging_bucket="test-staging",
            date_str="2026-03-29",
        )

        mock_client.export_documents.assert_called_once()
        call_args = mock_client.export_documents.call_args
        request = call_args[1]["request"]
        assert request["name"] == "projects/test-project/databases/(default)"
        assert "gs://test-staging/firestore/2026-03-29" in request["output_uri_prefix"]

    @patch("firestore_export.firestore_admin_v1.FirestoreAdminClient")
    def test_export_firestore_returns_summary(self, mock_client_class):
        from firestore_export import export_firestore

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock()
        mock_client.export_documents.return_value = mock_operation

        result = export_firestore("proj", "bucket", "2026-03-29")
        assert isinstance(result, str)
        assert "2026-03-29" in result


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd infrastructure/functions/backup_to_aws && python -m pytest test_firestore_export.py -v
```

Expected: FAIL (module not found)

- [ ] **Step 3: Implement firestore_export.py**

```python
"""Firestore export to GCS staging bucket."""

import logging
from google.cloud import firestore_admin_v1

logger = logging.getLogger(__name__)


def export_firestore(project: str, staging_bucket: str, date_str: str) -> str:
    """Export all Firestore collections to GCS.

    Args:
        project: GCP project ID.
        staging_bucket: GCS staging bucket name.
        date_str: ISO date string (YYYY-MM-DD) for the export folder.

    Returns:
        Summary string of the export.
    """
    client = firestore_admin_v1.FirestoreAdminClient()
    database_name = f"projects/{project}/databases/(default)"
    output_uri = f"gs://{staging_bucket}/firestore/{date_str}"

    logger.info(f"Starting Firestore export to {output_uri}")

    operation = client.export_documents(
        request={
            "name": database_name,
            "output_uri_prefix": output_uri,
        }
    )

    # Wait for export to complete (blocks until done)
    result = operation.result(timeout=1800)  # 30 min timeout

    logger.info(f"Firestore export complete: {output_uri}")
    return f"Exported to {output_uri} ({date_str})"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd infrastructure/functions/backup_to_aws && python -m pytest test_firestore_export.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add infrastructure/functions/backup_to_aws/firestore_export.py infrastructure/functions/backup_to_aws/test_firestore_export.py
git commit -m "feat: add Firestore export module for backup function"
```

---

## Task 8: Backup Cloud Function — BigQuery Export

**Files:**
- Create: `infrastructure/functions/backup_to_aws/bigquery_export.py`
- Create: `infrastructure/functions/backup_to_aws/test_bigquery_export.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for bigquery_export module."""

import unittest
from unittest.mock import MagicMock, patch


# Tables that should be exported weekly (daily-refresh)
DAILY_REFRESH_TABLES = [
    "karaokenerds_raw",
    "karaokenerds_community",
    "divebar_catalog",
    "kn_divebar_xref",
]

# Tables exported monthly
MONTHLY_TABLES = [
    "mb_artists",
    "mb_recordings",
    "mb_artist_tags",
    "mb_recording_isrc",
    "mbid_spotify_mapping",
    "mb_artists_normalized",
    "karaoke_recording_links",
    "mlhd_artist_similarity",
]


class TestBigQueryExport(unittest.TestCase):
    @patch("bigquery_export.bigquery.Client")
    def test_weekly_export_on_sunday(self, mock_client_class):
        from bigquery_export import export_bigquery_tables

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_job = MagicMock()
        mock_job.result.return_value = None
        mock_client.extract_table.return_value = mock_job

        result = export_bigquery_tables(
            project="test",
            staging_bucket="staging",
            date_str="2026-03-29",
            day_of_week=6,  # Sunday
            day_of_month=29,
        )

        # Should export daily-refresh tables on Sunday
        assert mock_client.extract_table.call_count == len(DAILY_REFRESH_TABLES)

    @patch("bigquery_export.bigquery.Client")
    def test_monthly_export_on_first(self, mock_client_class):
        from bigquery_export import export_bigquery_tables

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_job = MagicMock()
        mock_job.result.return_value = None
        mock_client.extract_table.return_value = mock_job

        result = export_bigquery_tables(
            project="test",
            staging_bucket="staging",
            date_str="2026-04-01",
            day_of_week=2,  # Tuesday (not Sunday)
            day_of_month=1,  # 1st of month
        )

        # Should export monthly tables only (not weekly since not Sunday)
        assert mock_client.extract_table.call_count == len(MONTHLY_TABLES)

    @patch("bigquery_export.bigquery.Client")
    def test_no_export_on_regular_day(self, mock_client_class):
        from bigquery_export import export_bigquery_tables

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        result = export_bigquery_tables(
            project="test",
            staging_bucket="staging",
            date_str="2026-03-25",
            day_of_week=1,  # Tuesday
            day_of_month=25,
        )

        mock_client.extract_table.assert_not_called()
        assert "skipped" in result.lower()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd infrastructure/functions/backup_to_aws && python -m pytest test_bigquery_export.py -v
```

- [ ] **Step 3: Implement bigquery_export.py**

```python
"""BigQuery table export to GCS staging bucket as Parquet."""

import logging
from google.cloud import bigquery

logger = logging.getLogger(__name__)

DATASET = "karaoke_decide"

# Exported weekly (every Sunday) — tables refreshed daily by ETL
WEEKLY_TABLES = [
    "karaokenerds_raw",
    "karaokenerds_community",
    "divebar_catalog",
    "kn_divebar_xref",
]

# Exported monthly (1st of month) — static or slowly changing
MONTHLY_TABLES = [
    "mb_artists",
    "mb_recordings",
    "mb_artist_tags",
    "mb_recording_isrc",
    "mbid_spotify_mapping",
    "mb_artists_normalized",
    "karaoke_recording_links",
    "mlhd_artist_similarity",
]


def export_bigquery_tables(
    project: str,
    staging_bucket: str,
    date_str: str,
    day_of_week: int,
    day_of_month: int,
) -> str:
    """Export BigQuery tables to GCS as Parquet based on schedule.

    Args:
        project: GCP project ID.
        staging_bucket: GCS staging bucket name.
        date_str: ISO date string for folder naming.
        day_of_week: 0=Monday, 6=Sunday.
        day_of_month: 1-31.

    Returns:
        Summary string.
    """
    tables_to_export = []

    if day_of_week == 6:  # Sunday
        tables_to_export.extend(
            (t, "daily-refresh") for t in WEEKLY_TABLES
        )

    if day_of_month == 1:  # 1st of month
        tables_to_export.extend(
            (t, "musicbrainz") for t in MONTHLY_TABLES
        )

    if not tables_to_export:
        logger.info("No BigQuery exports scheduled for today")
        return "Skipped — no exports scheduled"

    client = bigquery.Client(project=project)
    exported = []

    for table_name, prefix in tables_to_export:
        source = f"{project}.{DATASET}.{table_name}"
        dest_uri = f"gs://{staging_bucket}/bigquery/{prefix}/{date_str}/{table_name}/*.parquet"

        logger.info(f"Exporting {source} to {dest_uri}")

        job_config = bigquery.ExtractJobConfig(
            destination_format=bigquery.DestinationFormat.PARQUET,
        )

        job = client.extract_table(
            source=source,
            destination_uris=[dest_uri],
            job_config=job_config,
        )
        job.result(timeout=600)  # 10 min per table

        exported.append(table_name)
        logger.info(f"Exported {table_name}")

    return f"Exported {len(exported)} tables: {', '.join(exported)}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd infrastructure/functions/backup_to_aws && python -m pytest test_bigquery_export.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add infrastructure/functions/backup_to_aws/bigquery_export.py infrastructure/functions/backup_to_aws/test_bigquery_export.py
git commit -m "feat: add BigQuery export module with weekly/monthly scheduling"
```

---

## Task 9: Backup Cloud Function — GCS Sync and S3 Upload

**Files:**
- Create: `infrastructure/functions/backup_to_aws/gcs_sync.py`
- Create: `infrastructure/functions/backup_to_aws/s3_upload.py`

- [ ] **Step 1: Create gcs_sync.py**

```python
"""Sync new/changed GCS objects to the staging bucket."""

import logging
from datetime import datetime, timezone
from google.cloud import storage

logger = logging.getLogger(__name__)

# Only sync these prefixes from the source bucket (skip temp/uploads)
SYNC_PREFIXES = ["jobs/", "tenants/", "themes/"]


def sync_gcs_to_staging(
    source_bucket: str,
    staging_bucket: str,
    staging_prefix: str,
    max_objects: int = 5000,
) -> str:
    """Copy new/changed objects from source to staging bucket.

    Uses object metadata (updated time) to detect changes since last sync.
    The staging bucket stores a marker file with the last sync timestamp (ISO 8601).

    Args:
        source_bucket: Source GCS bucket name.
        staging_bucket: Staging GCS bucket name.
        staging_prefix: Prefix in staging bucket for synced files.
        max_objects: Max objects to sync per run (safety limit).

    Returns:
        Summary string.
    """
    client = storage.Client()
    src = client.bucket(source_bucket)
    dst = client.bucket(staging_bucket)

    # Read last sync marker as datetime
    marker_blob = dst.blob(f"{staging_prefix}.last_sync")
    last_sync_dt = None
    if marker_blob.exists():
        last_sync_str = marker_blob.download_as_text().strip()
        last_sync_dt = datetime.fromisoformat(last_sync_str)
        logger.info(f"Last sync: {last_sync_str}")

    copied = 0
    latest_updated_dt = last_sync_dt

    for prefix in SYNC_PREFIXES:
        blobs = src.list_blobs(prefix=prefix)
        for blob in blobs:
            if not blob.updated:
                continue

            if last_sync_dt and blob.updated <= last_sync_dt:
                continue

            # Copy to staging
            dst_path = f"{staging_prefix}{blob.name}"
            src.copy_blob(blob, dst, dst_path)
            copied += 1

            if latest_updated_dt is None or blob.updated > latest_updated_dt:
                latest_updated_dt = blob.updated

            if copied >= max_objects:
                logger.warning(f"Hit max_objects limit ({max_objects})")
                break

        if copied >= max_objects:
            break

    # Update sync marker
    if latest_updated_dt:
        marker_blob.upload_from_string(latest_updated_dt.isoformat())

    logger.info(f"Synced {copied} objects from {source_bucket}")
    return f"Synced {copied} objects"
```

- [ ] **Step 2: Create s3_upload.py**

```python
"""Upload files from GCS staging bucket to AWS S3."""

import json
import logging
import os

import boto3
from google.cloud import storage, secretmanager

logger = logging.getLogger(__name__)


def get_aws_credentials(project: str) -> dict:
    """Retrieve AWS credentials from GCP Secret Manager.

    Returns:
        Dict with access_key_id, secret_access_key, region.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/aws-backup-credentials/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return json.loads(response.payload.data.decode("utf-8"))


def upload_staging_to_s3(
    staging_bucket: str,
    s3_bucket: str,
    project: str = "nomadkaraoke",
) -> str:
    """Upload all objects in staging bucket to S3.

    Walks the staging bucket and uploads each object to the corresponding
    S3 key path. Skips marker files (.*).

    Args:
        staging_bucket: GCS staging bucket name.
        s3_bucket: S3 destination bucket name.
        project: GCP project for Secret Manager lookup.

    Returns:
        Summary string.
    """
    # Get AWS credentials
    aws_creds = get_aws_credentials(project)
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=aws_creds["access_key_id"],
        aws_secret_access_key=aws_creds["secret_access_key"],
        region_name=aws_creds.get("region", "us-east-1"),
    )

    gcs_client = storage.Client()
    bucket = gcs_client.bucket(staging_bucket)

    uploaded = 0
    errors = 0

    for blob in bucket.list_blobs():
        # Skip marker files
        if blob.name.startswith(".") or "/.last_sync" in blob.name:
            continue

        try:
            # Stream from GCS to S3 (avoids loading entire file into memory)
            with blob.open("rb") as gcs_file:
                s3_client.upload_fileobj(
                    Fileobj=gcs_file,
                    Bucket=s3_bucket,
                    Key=blob.name,
                )
            uploaded += 1

            # Delete from staging after successful upload
            blob.delete()

        except Exception as e:
            logger.error(f"Failed to upload {blob.name}: {e}")
            errors += 1

    summary = f"Uploaded {uploaded} files to s3://{s3_bucket}"
    if errors:
        summary += f" ({errors} errors)"
    logger.info(summary)
    return summary
```

- [ ] **Step 3: Commit**

```bash
git add infrastructure/functions/backup_to_aws/gcs_sync.py infrastructure/functions/backup_to_aws/s3_upload.py
git commit -m "feat: add GCS sync and S3 upload modules for backup function"
```

---

## Task 10: Backup Pulumi Infrastructure

**Files:**
- Create: `infrastructure/modules/backup.py`
- Modify: `infrastructure/__main__.py`

- [ ] **Step 1: Create backup.py**

```python
"""
Backup infrastructure module.

Creates:
- GCS staging bucket for backup exports
- Service account for backup Cloud Function
- Cloud Function v2 (Gen2) for nightly backups
- Cloud Scheduler job for daily trigger
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import (
    cloudfunctionsv2,
    cloudscheduler,
    serviceaccount,
    storage,
)

from config import PROJECT_ID, REGION, get_project_number


def create_backup_resources(all_secrets: dict) -> dict:
    """Create all backup infrastructure resources."""
    resources = {}

    # ==================== Service Account ====================

    sa = serviceaccount.Account(
        "backup-to-aws-sa",
        account_id="backup-to-aws",
        display_name="Backup to AWS Function",
        description="Service account for nightly backup Cloud Function",
    )
    resources["service_account"] = sa

    # Firestore export permission
    resources["firestore_export"] = gcp.projects.IAMMember(
        "backup-firestore-export",
        project=PROJECT_ID,
        role="roles/datastore.importExportAdmin",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # BigQuery read access
    resources["bq_viewer"] = gcp.projects.IAMMember(
        "backup-bigquery-viewer",
        project=PROJECT_ID,
        role="roles/bigquery.dataViewer",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    resources["bq_job_user"] = gcp.projects.IAMMember(
        "backup-bigquery-job-user",
        project=PROJECT_ID,
        role="roles/bigquery.jobUser",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Secret Manager access (for AWS credentials)
    resources["secret_accessor"] = gcp.projects.IAMMember(
        "backup-secret-accessor",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Logging
    resources["logging"] = gcp.projects.IAMMember(
        "backup-logging",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Staging Bucket ====================

    staging_bucket = storage.Bucket(
        "backup-staging-bucket",
        name="nomadkaraoke-backup-staging",
        location="US-CENTRAL1",
        force_destroy=True,  # Staging only — OK to destroy
        uniform_bucket_level_access=True,
        lifecycle_rules=[
            # Auto-delete staging files after 7 days (safety net)
            storage.BucketLifecycleRuleArgs(
                action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=storage.BucketLifecycleRuleConditionArgs(age=7),
            ),
        ],
    )
    resources["staging_bucket"] = staging_bucket

    # SA needs objectAdmin on staging bucket (read + write + delete)
    resources["staging_bucket_access"] = storage.BucketIAMMember(
        "backup-staging-bucket-access",
        bucket=staging_bucket.name,
        role="roles/storage.objectAdmin",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Firestore service agent needs write access to staging bucket for exports
    # (Firestore writes export files using its own service agent, not the caller's SA)
    resources["firestore_agent_staging_access"] = storage.BucketIAMMember(
        "backup-firestore-agent-staging-access",
        bucket=staging_bucket.name,
        role="roles/storage.objectAdmin",
        member=f"serviceAccount:service-{get_project_number()}@gcp-sa-firestore.iam.gserviceaccount.com",
    )

    # SA needs objectViewer on source buckets
    source_buckets = [
        "karaoke-gen-storage-nomadkaraoke",
        "nomadkaraoke-kn-data",
    ]
    for i, bucket_name in enumerate(source_buckets):
        resources[f"source_bucket_access_{i}"] = storage.BucketIAMMember(
            f"backup-source-bucket-access-{i}",
            bucket=bucket_name,
            role="roles/storage.objectViewer",
            member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
        )

    # ==================== Function Source Bucket ====================

    function_source_bucket = storage.Bucket(
        "backup-function-source",
        name=f"backup-to-aws-source-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=True,
        uniform_bucket_level_access=True,
    )
    resources["function_source_bucket"] = function_source_bucket

    # ==================== Cloud Function ====================

    function = cloudfunctionsv2.Function(
        "backup-to-aws-function",
        name="backup-to-aws",
        location=REGION,
        description="Nightly backup of Firestore, BigQuery, and GCS to AWS S3",
        build_config=cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="backup_to_aws",
            source=cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=function_source_bucket.name,
                    object="backup-to-aws-source.zip",
                ),
            ),
        ),
        service_config=cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory="1Gi",
            timeout_seconds=3600,  # 60 minutes
            min_instance_count=0,
            max_instance_count=1,
            service_account_email=sa.email,
            environment_variables={
                "GCP_PROJECT": PROJECT_ID,
                "STAGING_BUCKET": "nomadkaraoke-backup-staging",
                "S3_BUCKET": "nomadkaraoke-backup",
            },
            secret_environment_variables=[
                cloudfunctionsv2.FunctionServiceConfigSecretEnvironmentVariableArgs(
                    key="DISCORD_WEBHOOK_URL",
                    project_id=PROJECT_ID,
                    secret=all_secrets["discord-alert-webhook"].secret_id,
                    version="latest",
                ),
            ],
        ),
    )
    resources["function"] = function

    # Allow Cloud Scheduler to invoke the function
    resources["scheduler_invoker"] = cloudfunctionsv2.FunctionIamMember(
        "backup-scheduler-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Gen2 functions need Cloud Run invoker too
    resources["run_invoker"] = gcp.cloudrunv2.ServiceIamMember(
        "backup-sa-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Cloud Scheduler ====================

    scheduler = cloudscheduler.Job(
        "backup-scheduler",
        name="backup-to-aws-nightly",
        description="Trigger nightly backup to AWS S3",
        region=REGION,
        schedule="0 1 * * *",  # 1:00 AM ET daily
        time_zone="America/New_York",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=function.url,
            http_method="POST",
            oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=sa.email,
            ),
        ),
        retry_config=cloudscheduler.JobRetryConfigArgs(
            retry_count=2,
            min_backoff_duration="300s",
            max_backoff_duration="1800s",
        ),
    )
    resources["scheduler"] = scheduler

    return resources
```

- [ ] **Step 2: Verify `discord-alert-webhook` secret exists**

The backup module references `all_secrets["discord-alert-webhook"]`. Verify it exists in `infrastructure/modules/secrets.py`:

```bash
grep "discord-alert-webhook" infrastructure/modules/secrets.py
```

If missing, add `"discord-alert-webhook"` to the `secret_names` list in `secrets.py`.

- [ ] **Step 3: Wire into `__main__.py`**

Add near the end, before exports:

```python
from modules import backup

# ==================== Backup to AWS ====================
backup_resources = backup.create_backup_resources(all_secrets)
```

And add exports:

```python
# Backup to AWS
pulumi.export("backup_function_url", backup_resources["function"].url)
pulumi.export("backup_staging_bucket", backup_resources["staging_bucket"].name)
pulumi.export("backup_scheduler_name", backup_resources["scheduler"].name)
pulumi.export("backup_service_account", backup_resources["service_account"].email)
```

- [ ] **Step 3: Run `pulumi preview`**

```bash
cd infrastructure && pulumi preview --diff 2>&1 | head -80
```

Expected: Creates ~15 resources (SA, IAM bindings, buckets, function, scheduler). No updates/deletes.

- [ ] **Step 4: Run first `pulumi up` to create buckets and SA (function will fail — expected)**

```bash
cd infrastructure && pulumi up --yes
```

The function resource will fail because the source zip doesn't exist yet. The buckets and SA will be created. This is expected.

- [ ] **Step 5: Package and upload Cloud Function source**

```bash
cd infrastructure/functions/backup_to_aws
zip -r /tmp/backup-to-aws-source.zip *.py requirements.txt -x "test_*"
gsutil cp /tmp/backup-to-aws-source.zip gs://backup-to-aws-source-nomadkaraoke/backup-to-aws-source.zip
```

- [ ] **Step 6: Run second `pulumi up` to deploy the function**

```bash
cd infrastructure && pulumi up --yes
```

Now that the source zip exists, the Cloud Function will deploy successfully.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/modules/backup.py infrastructure/__main__.py
git commit -m "feat: add backup infrastructure — staging bucket, SA, Cloud Function, scheduler"
```

---

## Task 11: Document External Services Configuration

**Files:**
- Create: `docs/archive/2026-03-29-external-services-config.md`

- [ ] **Step 1: Document all external service configurations**

Create a file documenting OAuth redirect URIs, Cloudflare settings, and other external config needed for disaster recovery. This is a template — the user fills in actual values.

```markdown
# External Services Configuration (DR Reference)

**Purpose:** If GCP or other services are terminated, this documents what needs
to be re-created at the external service level. Actual secrets are in AWS Secrets Manager.

## Cloudflare

- **Account:** [email]
- **Zone:** nomadkaraoke.com
- **DNS Records:**
  - `api` → CNAME → `ghs.googlehosted.com` (proxied: false)
  - `gen` → CNAME → [Cloudflare Pages]
  - `decide` → CNAME → [GitHub Pages]
  - `vocalstar` → CNAME → [Cloudflare Pages]
  - `singa` → CNAME → [Cloudflare Pages]
- **Pages Projects:**
  - `karaoke-gen` — gen.nomadkaraoke.com
  - `karaoke-gen-tenant` — vocalstar/singa subdomains
- **Workers:**
  - `karaoke-decide-api-proxy` — proxies /api/* to Cloud Run

## Spotify Developer App

- **App name:** Nomad Karaoke Decide
- **Client ID:** [in Secret Manager: spotipy-client-id]
- **Redirect URIs:**
  - `https://decide.nomadkaraoke.com/api/services/spotify/callback`
  - `http://localhost:8000/api/services/spotify/callback`

## Google OAuth (for Flacfetch)

- **Account:** nomadflacfetch@gmail.com (no 2FA)
- **Used for:** YouTube cookies, Spotify "Login with Google"

## Stripe

- **Dashboard:** https://dashboard.stripe.com
- **Webhook endpoint:** `https://api.nomadkaraoke.com/api/users/webhooks/stripe`
- **Events:** `checkout.session.completed`, `checkout.session.expired`, `payment_intent.payment_failed`

## SendGrid

- **Sender emails:** gen@nomadkaraoke.com
- **Used by:** karaoke-gen (job notifications), karaoke-decide (magic links)

## AudioShake

- **API:** Used for lyrics transcription
- **Key:** [in Secret Manager: audioshake-api-key]

## Genius

- **API:** Used for reference lyrics lookup
- **Key:** [in Secret Manager: genius-api-key]

## KaraokeNerds

- **API:** Used for karaoke catalog sync
- **Key:** [in Secret Manager: karaokenerds-api-key]

## Dropbox

- **OAuth app:** Used for karaoke file distribution
- **Credentials:** [in Secret Manager: dropbox-oauth-credentials]

## YouTube

- **OAuth app:** Used for karaoke video uploads
- **Credentials:** [in Secret Manager: youtube-oauth-credentials, youtube-client-credentials]
```

- [ ] **Step 2: Ask user to fill in actual values and verify**

The template has placeholders. User should verify DNS records, redirect URIs, etc. are accurate.

- [ ] **Step 3: Commit**

```bash
git add docs/archive/2026-03-29-external-services-config.md
git commit -m "docs: add external services configuration reference for DR"
```

---

## Task 12: Create Restore Runbook

**Files:**
- Create: `docs/DISASTER-RECOVERY.md`

- [ ] **Step 1: Write the restore runbook**

```markdown
# Disaster Recovery Runbook

## Quick Reference

| Data | Location | Recovery Method |
|------|----------|----------------|
| Firestore | S3 `firestore/YYYY-MM-DD/` | Import to new GCP project |
| BigQuery (weekly) | S3 `bigquery/daily-refresh/` | Load Parquet files |
| BigQuery (monthly) | S3 `bigquery/musicbrainz/` | Load Parquet files |
| BigQuery (Spotify) | S3 `bigquery/spotify/` | Load from Glacier Deep Archive |
| GCS job files | S3 `gcs/job-files/` | Sync to new bucket |
| Secrets | AWS Secrets Manager | Decrypt with KMS, configure new project |

## Scenario 1: Accidental Data Deletion (GCP still available)

### Firestore
1. Check if within 7-day PITR window
2. Use `gcloud firestore databases restore` to restore to point-in-time
3. Or import from latest nightly S3 backup

### BigQuery
1. Check if within 7-day time-travel window
2. Use `SELECT * FROM dataset.table FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)` to query historical data
3. Or load from latest S3 Parquet backup

### GCS
1. Check if object versioning has the deleted object
2. Use `gsutil ls -a gs://bucket/path` to list versions
3. Or sync from S3 backup

## Scenario 2: Complete GCP Project Loss

### Prerequisites
- AWS credentials from password manager
- AWS KMS key ARN from password manager
- This repo (from GitHub)

### Step-by-Step Recovery

1. **Create new GCP project**
   ```bash
   gcloud projects create nomadkaraoke-v2 --name="Nomad Karaoke"
   gcloud config set project nomadkaraoke-v2
   ```

2. **Retrieve AWS credentials**
   Get from password manager: AWS access key, secret key, KMS key ARN

3. **Restore Firestore**
   ```bash
   # Download latest export from S3
   aws s3 sync s3://nomadkaraoke-backup/firestore/LATEST_DATE/ /tmp/firestore-restore/
   # Upload to new GCS bucket
   gsutil -m cp -r /tmp/firestore-restore/ gs://NEW_BUCKET/firestore-import/
   # Import
   gcloud firestore import gs://NEW_BUCKET/firestore-import/
   ```

4. **Restore BigQuery**
   ```bash
   # Download Parquet exports
   aws s3 sync s3://nomadkaraoke-backup/bigquery/ /tmp/bq-restore/
   # Create dataset
   bq mk --dataset nomadkaraoke-v2:karaoke_decide
   # Load each table
   bq load --source_format=PARQUET karaoke_decide.TABLE_NAME /tmp/bq-restore/TABLE_NAME/*.parquet
   ```
   **Cost note:** Retrieving from Glacier Deep Archive takes 12-48 hours and costs ~$150 (bulk) to ~$750 (standard).

5. **Restore GCS files**
   ```bash
   aws s3 sync s3://nomadkaraoke-backup/gcs/job-files/ /tmp/gcs-restore/
   gsutil -m cp -r /tmp/gcs-restore/ gs://NEW_BUCKET/
   ```

6. **Restore secrets**
   ```bash
   # Retrieve from AWS Secrets Manager, decrypt with KMS
   aws secretsmanager get-secret-value --secret-id nomadkaraoke-secrets
   # Create each secret in new project
   echo -n "VALUE" | gcloud secrets create SECRET_NAME --data-file=-
   ```

7. **Rebuild infrastructure**
   ```bash
   cd infrastructure
   # Update config.py with new project ID
   pulumi config set gcp:project nomadkaraoke-v2
   pulumi up
   ```

8. **Deploy services**
   - Push to GitHub to trigger CI/CD
   - Or manually deploy via `gcloud run deploy`

9. **Update DNS**
   - Update Cloudflare DNS records to point to new Cloud Run URLs

10. **Re-register OAuth apps**
    - See `docs/archive/2026-03-29-external-services-config.md`
    - Update redirect URIs at Spotify, Google, Dropbox
```

- [ ] **Step 2: Commit**

```bash
git add docs/DISASTER-RECOVERY.md
git commit -m "docs: add disaster recovery runbook with restore procedures"
```

---

## Task 13: One-Time Bulk Exports (Phase 3)

**⚠️ This task costs ~$900 in GCP egress. User must approve before proceeding.**

- [ ] **Step 1: Export BigQuery Spotify tables to GCS staging**

```bash
# Export large Spotify tables as Parquet (split into multiple files)
for table in spotify_tracks spotify_albums spotify_artists spotify_artist_genres \
  spotify_track_artists spotify_audio_features spotify_audio_analysis_tracks \
  spotify_audio_analysis_sections spotify_tracks_full spotify_tracks_normalized \
  spotify_artists_normalized; do
  echo "Exporting $table..."
  bq extract --destination_format=PARQUET \
    "nomadkaraoke:karaoke_decide.$table" \
    "gs://nomadkaraoke-backup-staging/bigquery/spotify/$table/*.parquet"
done
```

- [ ] **Step 2: Set up GCP Storage Transfer to S3**

Use GCP Storage Transfer Service for the large transfers (handles 7+ TB efficiently):

```bash
# Create transfer job: raw archives (7.2 TB) → S3 Glacier Deep Archive
gcloud transfer jobs create \
  gs://nomadkaraoke-raw-archives \
  s3://nomadkaraoke-backup/gcs/raw-archives/ \
  --name=bulk-raw-archives-transfer
```

**Note:** This transfer will take several hours and cost ~$900 in egress. Monitor progress in the GCP Console under Storage Transfer.

- [ ] **Step 3: Sync MusicBrainz and MLHD buckets**

```bash
# These are smaller, gsutil is fine
gsutil -m rsync -r gs://nomadkaraoke-musicbrainz-data/ gs://nomadkaraoke-backup-staging/gcs/musicbrainz-data/
gsutil -m rsync -r gs://nomadkaraoke-mlhd-data/ gs://nomadkaraoke-backup-staging/gcs/mlhd-data/

# Then use the backup function's S3 upload, or direct boto3 script
```

- [ ] **Step 4: Export and encrypt secrets**

```bash
# Script to export all secrets and store in AWS Secrets Manager
python3 << 'SCRIPT'
import json
from google.cloud import secretmanager
import boto3

gcp_client = secretmanager.SecretManagerServiceClient()
project = "nomadkaraoke"

# List all secrets
secrets = {}
parent = f"projects/{project}"
for secret in gcp_client.list_secrets(request={"parent": parent}):
    name = secret.name.split("/")[-1]
    try:
        version = gcp_client.access_secret_version(
            request={"name": f"{secret.name}/versions/latest"}
        )
        secrets[name] = version.payload.data.decode("utf-8")
    except Exception as e:
        secrets[name] = f"ERROR: {e}"
        print(f"Could not access {name}: {e}")

# Store in AWS Secrets Manager (encrypted by KMS)
aws_client = boto3.client("secretsmanager", region_name="us-east-1")
aws_client.create_secret(
    Name="nomadkaraoke-gcp-secrets",
    SecretString=json.dumps(secrets),
)
print(f"Exported {len(secrets)} secrets to AWS Secrets Manager")
SCRIPT
```

- [ ] **Step 5: Store KMS key ARN in password manager**

Note the KMS key ARN used by AWS Secrets Manager and store it in your password manager.

---

## Task 14: External Backup Freshness Monitor

**⚠️ This is set up in AWS, not GCP.**

- [ ] **Step 1: Create a simple AWS Lambda to check S3 freshness**

This is a minimal Lambda that checks if the latest Firestore backup is less than 48 hours old and alerts via SNS (email) if stale.

The Lambda, SNS topic, and CloudWatch Events rule can be created via AWS Console or AWS CLI. This is a simple enough setup that a manual console creation is fine — it's a safety net, not a complex service.

Key config:
- **Trigger:** CloudWatch Events, daily at 6 AM ET
- **Check:** List `s3://nomadkaraoke-backup/firestore/` prefix, check latest folder date
- **Alert:** If latest is >48h old, send SNS email notification

- [ ] **Step 2: Subscribe to SNS alert**

```bash
aws sns create-topic --name backup-freshness-alert --profile nomadkaraoke-backup
aws sns subscribe --topic-arn ARN --protocol email --notification-endpoint YOUR_EMAIL --profile nomadkaraoke-backup
```

---

## Task 15: Final Verification and Testing

- [ ] **Step 1: Trigger a manual backup run**

```bash
# Invoke the Cloud Function manually
curl -X POST $(gcloud functions describe backup-to-aws --region=us-central1 --format='value(url)' --project=nomadkaraoke) \
  -H "Authorization: bearer $(gcloud auth print-identity-token)"
```

- [ ] **Step 2: Verify Firestore export in S3**

```bash
aws s3 ls s3://nomadkaraoke-backup/firestore/ --profile nomadkaraoke-backup
```

Expected: Shows today's date folder.

- [ ] **Step 3: Verify Discord notification received**

Check the Discord channel for the backup alert.

- [ ] **Step 4: Test restore (Firestore)**

```bash
# Create a test GCP project or use Firestore emulator
# Download the export
aws s3 sync s3://nomadkaraoke-backup/firestore/LATEST/ /tmp/test-restore/
# Verify the export contains expected collections
ls /tmp/test-restore/
```

- [ ] **Step 5: Commit final state**

```bash
git add -A
git commit -m "feat: complete business continuity implementation — all phases"
```

- [ ] **Step 6: Update spec status**

In `docs/archive/2026-03-27-business-continuity-design.md`, change:
```
**Status:** Design approved, pending implementation
```
to:
```
**Status:** Implemented
```
