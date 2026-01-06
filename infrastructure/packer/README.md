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

## Runtime Behavior

When the VM boots with this image:

1. **Startup script** (`/opt/encoding-worker/startup.sh`) runs via `ExecStartPre`:
   - Fetches API key from Secret Manager
   - Downloads latest `karaoke-gen` wheel from GCS
   - Installs wheel into venv

2. **Encoding worker service** starts via `ExecStart`:
   - Runs `uvicorn backend.services.gce_encoding.main:app`
   - Listens on port 8080

## When to Rebuild

Rebuild the image when:

- Python version needs updating
- FFmpeg version needs updating
- New system packages required
- Font packages change

**No rebuild needed** for application code changes - the wheel is downloaded at runtime.

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
