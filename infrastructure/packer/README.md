# Encoding Worker Packer Image

Custom GCE machine image for the encoding worker VM with all dependencies pre-installed.

## Why?

The encoding worker previously took **~10 minutes** to start because it built Python 3.13 from source on every boot. With this pre-baked image, startup time is reduced to **~30 seconds**.

| Component | Before (startup script) | After (Packer image) |
|-----------|------------------------|---------------------|
| Python 3.13 build | ~7 min (every boot) | Pre-installed |
| FFmpeg install | ~1 min (every boot) | Pre-installed |
| System packages | ~1 min (every boot) | Pre-installed |
| Font packages | ~30 sec (every boot) | Pre-installed |
| API key + wheel | ~30 sec | ~30 sec |
| **Total** | **~10 min** | **~30 sec** |

## Prerequisites

1. **Packer installed**: `brew install packer` or [download from HashiCorp](https://www.packer.io/downloads)
2. **GCP authentication**: `gcloud auth application-default login`
3. **Required permissions**: `roles/compute.instanceAdmin.v1` and `roles/iam.serviceAccountUser`

## Building the Image

```bash
cd infrastructure/packer

# Initialize Packer plugins (first time only)
packer init encoding-worker.pkr.hcl

# Build the image
packer build encoding-worker.pkr.hcl
```

Build takes approximately **15-20 minutes** (mostly Python compilation with optimizations).

### Build Variables

Override defaults with `-var`:

```bash
packer build \
  -var 'python_version=3.13.2' \
  -var 'zone=us-west1-a' \
  encoding-worker.pkr.hcl
```

| Variable | Default | Description |
|----------|---------|-------------|
| `project_id` | `nomadkaraoke` | GCP project ID |
| `zone` | `us-central1-a` | Build zone |
| `python_version` | `3.13.1` | Python version to install |

## What's Included

The image contains:

- **Python 3.13** at `/opt/python313` (built with optimizations)
- **FFmpeg 7.x** at `/usr/local/bin/ffmpeg` (John Van Sickle static build)
- **Noto fonts** including CJK support for subtitle rendering
- **Virtual environment** at `/opt/encoding-worker/venv`
- **Systemd service** `encoding-worker.service` (enabled, not started)
- **Startup script** `/opt/encoding-worker/startup.sh`

## Runtime Behavior (Immutable Deployment Pattern)

When the VM boots with this image:

1. **bootstrap.sh** runs via `ExecStartPre`:
   - Downloads `startup.sh` from GCS (CI-managed, always current)
   - Executes the downloaded startup script

2. **startup.sh** (downloaded from GCS):
   - Fetches API key from Secret Manager
   - Downloads `karaoke_gen-current.whl` from GCS (fixed path, no sorting!)
   - Installs wheel into venv
   - **Verifies** installed version matches `version.txt` from GCS
   - Fails fast if version mismatch

3. **Encoding worker service** starts via `ExecStart`:
   - Runs `uvicorn backend.services.gce_encoding.main:app`
   - Listens on port 8080

See `infrastructure/encoding-worker/README.md` for details on this pattern.

## When to Rebuild

Rebuild the image when:

- Python version needs updating
- FFmpeg version needs updating
- New system packages required
- Font packages change
- bootstrap.sh changes (extremely rare)

**Image disk size:** `disk_size` in the template (30 GB) is the *minimum* boot-disk
size of any VM created from the image — GCE cannot create a disk smaller than its
source image. Keep it ≤ `DiskSizes.ENCODING_WORKER` in `infrastructure/config.py`
(50 GB); `infrastructure/test_encoding_worker_config.py` enforces this.

**After a rebuild, existing VMs keep their old disks** (Pulumi only uses the new
image when a VM/disk is recreated). To roll a new image onto the stopped workers
without re-allocating VMs (stockout-safe), use the in-place boot-disk swap in
`docs/archive/2026-09-26-encoding-worker-disk-cost.md`.

**No rebuild needed** for:
- Application code changes (wheel downloaded at runtime)
- Startup logic changes (startup.sh downloaded from GCS)

## Image Family

Images are created with family `encoding-worker`. Pulumi uses the family to always get the latest image:

```python
image=f"projects/nomadkaraoke/global/images/family/encoding-worker"
```

Old images are automatically replaced in the family, but retained for rollback.

## Rollback

If the new image has issues:

1. Find previous image: `gcloud compute images list --filter="family:encoding-worker"`
2. Update Pulumi to use specific image instead of family
3. Or rebuild from a previous git commit

## CI Integration

For automated rebuilds, add a GitHub Actions workflow that triggers on changes to `infrastructure/packer/`:

```yaml
name: Build Encoding Worker Image
on:
  push:
    branches: [main]
    paths:
      - 'infrastructure/packer/**'
  workflow_dispatch:
```

See `PHASE5-PACKER-IMAGE-PLAN.md` for full workflow example.
