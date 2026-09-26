# Encoding-worker disk cost reduction (2026-09-26)

## Problem

The 10 encoding-worker VMs (c4d primary pair + 8 stockout fallbacks) are stopped
between jobs, so their cost is mostly their boot disks. Last-30-day billing:
Hyperdisk Balanced capacity ~$48 (601 GiB-mo) + provisioned throughput ~$12 +
provisioned IOPS ~$6; Balanced PD capacity ~$45 (includes 2 runner disks). Plus
~$18/mo "Storage Image" for 39 custom images.

## Findings

- **Disks were ~85% empty.** `encoding-worker-b`, 8 months in service: 15 GB used
  of 99 GB (7 GB venv, 3 GB pip cache, 0.7 GB logs). Fresh disks from the current
  image use 8 GB.
- **Scratch need is small.** Each `/encode` downloads its whole `jobs/<id>/` folder
  into `/tmp` on the boot disk. Across 1,904 job folders: median 0.43 GB, p99 1.3 GB,
  max 4.0 GB. Downloads happen before the (serialized) heavy lane, so queued jobs
  hold their inputs at the same time. The deepest queue seen in 30 days was 8.
  Realistic peak: ~8 GB base + ~10-15 GB scratch.
- **Only two disks had paid IOPS/throughput.** `-c4a` and `-n4db`, created 2026-08-15
  after the June IOPS tune, got the Hyperdisk defaults of 3600 IOPS / 290 MB/s. The
  other hyperdisks were already on the free baseline of 3000 / 140.
- **Image size set the disk floor.** Packer built the image with `disk_size = 100`.
  GCE cannot create a disk smaller than its source image, so every worker had to be
  at least 100 GB.
- **Runner image pruning had never run.** The monthly `build-runner-images.yml`
  step used an invalid filter (`-deprecated.state:*`). gcloud wrote the error to
  stderr and the step printed "Nothing to deprecate". Deprecating wouldn't have
  saved money anyway, because deprecated images are still billed.

## Changes

| Lever | Change | Est. saving |
|---|---|---|
| Boot disk size | 100 GB to **50 GB** on all 10 workers (`DiskSizes.ENCODING_WORKER`); Packer image `disk_size` 100 to **30** | ~$44/mo |
| Hyperdisk IOPS/throughput | `-c4a`/`-n4db` 3600/290 to 3000/140 (free baseline). New hyperdisks are created at 3000/140 explicitly | ~$18/mo |
| Custom images | Deleted 23 old runner images (kept newest 2 per family). Fixed the prune step so it deletes beyond the newest 2, matches the family exactly, and skips images that are a disk's source | ~$13/mo |

**Total: ~$75/mo.**

Levers considered and **not** taken:
- **Removing fallback VMs.** Not done. They are the stockout insurance, and machine-family diversity is deliberate.
- **Ephemeral boot disks (create from image at start).** Not done. This adds disk-creation and hydration latency to every cold start, plus another failure mode during stockouts. The saving (~$4/VM/mo after the shrink) isn't worth that.
- **pd-standard for the fallbacks.** Not done. A 50 GB pd-standard disk gets ~75 read IOPS, which would slow boot and pip installs badly. c4/c4d/n4d VMs require Hyperdisk anyway.
- **Smaller than 50 GB.** Not done. It saves only ~$1/disk/mo per 10 GB, and a disk-full error during a bulk-queue encode costs far more than that.

## Performance

pd-balanced performance scales with size: 100 GB gives 3600 IOPS / 168 MB/s, and
50 GB gives 3300 / 154. Hyperdisk Balanced at 3000 / 140 is the same at any size.
Encoding is CPU-bound: a full 4K finalization writes ~200 MB of outputs in ~2 min.

Real full `/encode` of the same job (`bench/disk-swap-input`, the Aug-2026 benchmark
song; formats 4k lossless + 4k lossy + 720p), wall time in seconds:

| VM | CPU platform | Before (100 GB, old image) | After (50 GB, new image) | start to healthy |
|---|---|---|---|---|
| fallback-c2df (pd-balanced) | AMD Milan | 142.6, 138.9 | 142.2, 136.9 | 34.9 s to 33.5 s |
| fallback-c4a (hyperdisk) | Intel Emerald Rapids | 132.1, 121.4 | 147.4, 144.5 (first boot of a fresh disk) | 39.1 s to 53.3 s |
| fallback-n2da (pd-balanced) | AMD Milan | failed: transient metadata-server SSL error (pre-existing, see v0.217.0) | 155.7 | to 41.7 s |
| fallback-n2f (pd-balanced) | Intel Cascade Lake | 247.2 (Aug benchmark) | 246.8 | to 39.7 s |
| fallback-a (hyperdisk) | AMD Turin (c4d) | n/a | 99.0 | to 42.3 s |

## Rollout procedure: in-place boot-disk swap

Changing `boot_disk.initialize_params.size` makes Pulumi either replace the VM or
try an in-place resize. An in-place resize can't shrink a disk, and a replacement
has to re-allocate the VM, which for c4d/n4d/c2d can be blocked for hours by a
stockout. Each VM was left in place and only its boot disk was swapped, while the
VM was `TERMINATED`. No compute capacity is needed for this.

```bash
# Per VM, only if status == TERMINATED and config/encoding-worker.deploy_in_progress is false
gcloud compute instances detach-disk $VM --zone=$Z --disk=$VM
gcloud compute disks delete $VM --zone=$Z --quiet
gcloud compute disks create $VM --zone=$Z --image=$NEW_IMAGE --size=50GB --type=$TYPE \
  [--provisioned-iops=3000 --provisioned-throughput=140]   # hyperdisk-balanced only
gcloud compute instances attach-disk $VM --zone=$Z --disk=$VM --boot --device-name=persistent-disk-0
gcloud compute instances set-disk-auto-delete $VM --zone=$Z --disk=$VM --auto-delete
# then: pulumi refresh --target <instance urn>  → state picks up size 50 / new image;
#       pulumi preview shows no diff for that VM (the family-image URL diff-suppresses).
```

Rollback: repeat the swap using the previous image. `encoding-worker-1786843294`
is kept for this.

Verification per VM: start it, check `/health` (wheel version), run a real `/encode`,
check `df`, then stop it. VMs that hit a stockout at start were swapped but could
only be verified later.

## Status at time of writing

See the PR description for which VMs are swapped and verified.
