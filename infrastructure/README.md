# Infrastructure as Code with Pulumi

This directory contains Pulumi infrastructure code that manages all Google Cloud Platform resources for the karaoke generator backend.

## Directory Structure

```
infrastructure/
├── __main__.py              # Entry point - wires modules together (~340 lines)
├── config.py                # Centralized configuration constants
├── Pulumi.yaml              # Pulumi project config
├── Pulumi.prod.yaml         # Production stack config
├── requirements.txt         # Python dependencies
│
├── modules/                 # Core GCP resources
│   ├── database.py         # Firestore database + indexes
│   ├── storage.py          # GCS bucket + lifecycle rules
│   ├── artifact_registry.py # Docker repository
│   ├── secrets.py          # Secret Manager secrets
│   ├── cloud_tasks.py      # Task queues
│   ├── cloud_run.py        # Domain mapping, Cloud Run Jobs
│   ├── monitoring.py       # Alert policies
│   └── iam/                # IAM by purpose
│       ├── backend_sa.py   # karaoke-backend service account
│       ├── github_actions_sa.py # WIF + deployer permissions
│       ├── claude_automation_sa.py # Claude Code automation
│       └── worker_sas.py   # VM service accounts
│
├── compute/                 # VM-based resources
│   ├── flacfetch_vm.py     # Audio download service
│   ├── encoding_worker_vm.py # Video encoding VM
│   ├── github_runners.py   # Self-hosted CI runners
│   └── startup_scripts/    # VM startup scripts (.sh)
│       ├── flacfetch.sh
│       ├── encoding_worker.sh
│       └── github_runner.sh
│
├── functions/               # Cloud Functions
│   └── gdrive_validator/
│       ├── main.py
│       ├── requirements.txt
│       └── deploy.sh       # Source upload script
│
└── monitoring/              # Monitoring configs
    ├── dashboard.json
    └── job-inspector.html
```

## Resources Managed

| Category | Resources |
|----------|-----------|
| **Database** | Firestore (Native mode), 10 indexes, TTL policy |
| **Storage** | GCS bucket with lifecycle rules |
| **Registry** | Artifact Registry for Docker images |
| **Secrets** | 18 Secret Manager secrets |
| **IAM** | 7 service accounts, WIF, 50+ IAM bindings |
| **Compute** | Encoding worker, Flacfetch, 10 GitHub runners |
| **Tasks** | 5 Cloud Tasks queues |
| **Monitoring** | 4 alert policies |
| **Functions** | GDrive validator + scheduler |

## Quick Start

```bash
cd infrastructure

# Preview changes
pulumi preview --diff

# Apply changes
pulumi up

# View outputs
pulumi stack output
```

## Prerequisites

1. **Pulumi CLI**: `brew install pulumi`
2. **Pulumi Account**: Sign up at https://app.pulumi.com
3. **Google Cloud SDK**: With authenticated account
4. **Python 3.10+**: For running Pulumi

## Module Guide

### config.py
Centralized configuration for the entire infrastructure:
- Project ID, region, zone
- Machine types and disk sizes
- Google Drive folder ID
- Number of GitHub runners

### modules/database.py
Firestore database and composite indexes. Indexes are required for queries that filter/order by multiple fields.

### modules/storage.py
GCS bucket with lifecycle rules:
- 7-day cleanup of staging data
- 24-hour cleanup of raw audio files

### modules/iam/
Service accounts organized by purpose:
- `backend_sa.py`: Cloud Run backend permissions
- `github_actions_sa.py`: CI/CD via Workload Identity Federation
- `worker_sas.py`: VM service accounts (flacfetch, encoding, runners)

### compute/
VM definitions with startup scripts as separate files. Each module creates:
- Static IP (where needed)
- VM instance
- Firewall rules

## Adding Secret Values

After `pulumi up`, add actual secret values:

```bash
# API keys
echo -n "key" | gcloud secrets versions add audioshake-api-key --data-file=-
echo -n "key" | gcloud secrets versions add genius-api-key --data-file=-

# Private tracker credentials
echo -n "key" | gcloud secrets versions add red-api-key --data-file=-
echo -n "url" | gcloud secrets versions add red-api-url --data-file=-

# GitHub runner PAT (repo scope)
echo -n "ghp_xxx" | gcloud secrets versions add github-runner-pat --data-file=-
```

## GDrive Validator

The Cloud Function validates your public Google Drive folder daily:
- Checks for duplicate sequence numbers
- Validates filename formats
- Detects sequence gaps
- Sends Pushbullet notifications

**Deploy:**
```bash
cd functions/gdrive_validator
./deploy.sh
```

Note: `deploy.sh` uploads the source ZIP to GCS. Pulumi creates the function resources but doesn't handle source upload.

## Self-Hosted GitHub Runners

20 GCP VMs serve as self-hosted runners with:
- 200GB SSD (vs 14GB on GitHub-hosted)
- Pre-installed: Docker, Python 3.13, Node 20, Java 21, FFmpeg
- Labels: `self-hosted`, `linux`, `x64`, `gcp`, `large-disk`

**Usage in workflows:**
```yaml
runs-on: [self-hosted, linux, gcp]
```

**Troubleshooting:**
```bash
# View startup logs
gcloud compute ssh github-runner-1 --zone=us-central1-a -- \
  "sudo cat /var/log/github-runner-startup.log"

# Check runner service
gcloud compute ssh github-runner-1 --zone=us-central1-a -- \
  "sudo systemctl status actions.runner.*"
```

## Encoding Worker

High-performance VM for video encoding:
- Machine: c4-standard-8 (8 vCPU, 32GB RAM)
- Disk: 100GB hyperdisk-balanced
- Pre-installed: FFmpeg (static build), Python 3.13, Noto fonts

**API Endpoints:**
- `POST /encode` - Full encoding job
- `POST /encode-preview` - Quick 480p preview
- `GET /status/{job_id}` - Job status
- `GET /health` - Health check

**Troubleshooting:**
```bash
gcloud compute ssh encoding-worker --zone=us-central1-c -- \
  "sudo systemctl status encoding-worker"
```

## State Management

Pulumi state is stored in Pulumi Cloud. To sync with actual cloud resources:

```bash
pulumi refresh
```

## Multi-Environment

```bash
pulumi stack init dev
pulumi config set gcp:project nomadkaraoke-dev
pulumi up

pulumi stack select prod  # Switch stacks
```

## Troubleshooting

### "Resource already exists"
Import manually-created resources:
```bash
pulumi import gcp:storage/bucket:Bucket karaoke-storage karaoke-gen-storage-nomadkaraoke
```

### State conflicts
```bash
pulumi refresh  # Sync state with cloud
```

### Permissions errors
Ensure your gcloud account has Owner or Editor role, or specific roles like Cloud Build Editor, Storage Admin.

## Future Improvements

### Phase 5: Custom Machine Images (Planned)

The encoding worker currently takes ~10 minutes to start because it builds Python 3.13 from source on every boot. A Packer-built custom image would reduce this to ~30 seconds.

**See:** [`docs/PHASE5-PACKER-IMAGE-PLAN.md`](docs/PHASE5-PACKER-IMAGE-PLAN.md) for the implementation plan.

Benefits:
- Startup time: 10 min → 30 sec
- Pre-baked: Python 3.13, FFmpeg, fonts
- Hot updates: karaoke-gen wheel still downloaded at runtime
