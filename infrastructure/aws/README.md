# nomad-aws: Nomad Karaoke's AWS account (790139457751)

This stack holds the **AWS encoding worker**, an on-demand EC2 instance running the same `gce_encoding` HTTP service as the GCE encoding VMs. It is paid for by AWS Activate credits ($5k, expiring 2028-10-31). The plan and benchmark are in `docs/archive/2026-10-29-aws-encoding-worker-plan.md`.

## What's here

| Resource | Purpose |
|---|---|
| `aws-encoding-worker-1` | c7a.4xlarge **on-demand**, us-east-2b. Stops on OS shutdown (idle watchdog); the EBS root survives. Spot capacity was too thin (see `__main__.py`). The Elastic IP serves `:8080`. |
| `nomad-encoding-worker` role | The box's identity. GCP federates it to `encoding-worker@nomadkaraoke` (WIF pool `aws-encoding`, in the GCP stack's `modules/aws_encoding_wif.py`). It also has SSM for a shell. |
| `nomad-encoding-controller` user | Backend rights: start/stop instances tagged `Project=nomad-encoding`, plus describe. |

`provision.sh` (in `infrastructure/encoding-worker/aws/`) runs once as user-data. Every boot after that runs `bootstrap.sh` → `startup-aws.sh`, which CI uploads to GCS. The new wheel installs at boot and per job, so there is **no blue-green deploy** for this box. `idle-watchdog.py` stops the box when it's idle.

## Apply

`pulumi up` runs locally and never in CI, with Nomad's AWS keys from the workspace `.envrc`:

```bash
cd infrastructure/aws && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
AWS_ACCESS_KEY_ID=$NOMAD_AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$NOMAD_AWS_SECRET_ACCESS_KEY \
  pulumi up --stack nomadkaraoke/prod
```

`ami` and `user_data` are `ignore_changes`, so a newer Ubuntu or an edited `provision.sh` never replaces the worker's disk. To re-provision on purpose, use `pulumi up --replace <instance urn>`.

## One-time wiring after the first `up`

1. **Controller key → GCP secret** (needs `/gcp-admin`):
   ```bash
   aws iam create-access-key --user-name nomad-encoding-controller \
     | jq -c '{access_key_id:.AccessKey.AccessKeyId, secret_access_key:.AccessKey.SecretAccessKey}' \
     | scripts/gcp-admin gcloud secrets versions add aws-encoding-controller-credentials --data-file=- --project=nomadkaraoke
   ```
2. **Register the worker in the pool:** append `pulumi stack output fallback_vms_entry` to the JSON list in secret `encoding-worker-fallback-vms`. The backend picks up `:latest` on new instances, and the next deploy picks it up everywhere.
3. **Rank:** by default the AWS worker is a **last resort** (`SPEED_RANK["c7a"] = 90`). To prefer it, set Firestore `config/encoding-worker.speed_rank_overrides = {"c7a": 5}`. Delete the field to roll back; no deploy is needed either way.

## Operating it

- **Shell:** `aws ssm start-session --target <instance_id> --region us-east-2`.
- **Logs:** `journalctl -u encoding-worker`, `/var/log/encoding-worker-startup.log`, `journalctl -u encoding-worker-idle`. They are **not** in Cloud Logging.
- **Health:** `curl http://<eip>:8080/health`.
- **Capacity:** if c7a capacity is short, the start fails with `InsufficientInstanceCapacity`; that is recorded in `capacity_state`, demoted for 15 min, and GCE takes over.
