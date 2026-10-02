# Postmark IP block: current mitigation, and options if we need more

**Date:** 2026-10-02 · **Status:** mitigated (SMTP fallback, $0). Static egress IP **deferred** to save money; revisit only if the alert below fires.
**Applies to:** `karaoke-backend` (this repo) and `karaoke-decide` (both Cloud Run, us-central1).

## TL;DR

- Postmark's network firewall blocks some of Google Cloud's **shared us-central1 egress IPs** for API sends. They return a bare **nginx HTML 403**, not Postmark JSON. Postmark support confirmed the cause on ticket #11562402: the IP "has previously been associated with malicious activity, and has been blocked. We do not lift these blocks ourselves."
- **Current mitigation (free):** on that HTML 403, both services resend the email through **Postmark SMTP** (`smtp.postmarkapp.com:587`). This is proven in prod: on 2026-10-02 between 19:10 and 20:17 UTC, 12 of 12 blocked sends were delivered via SMTP, each ~0.5s slower, with 0 failures.
- **Tripwire (free until 2027-09-01, then $0.35/mo):** a Cloud Monitoring alert, **"Email - Postmark SMTP fallback failing (emails being lost)"**, emails andrew@beveridge.uk if an SMTP-fallback send ever fails.
- **If that alert fires**, Postmark is probably blocking SMTP from our IPs too. Implement **Option A** below (reuse the existing Cloud NAT with a reserved static IP). Estimated cost: **~$4–7/month**.

## Background & evidence

- Onset: 2026-09-30 05:26 UTC. Every gen backend send failed from 2026-10-02 11:25 UTC until the fallback shipped. Lost emails included magic-link logins, made-for-you order confirmations, and admin alerts. Impacted users got an apology plus 1 credit (see the workspace session record `docs/sessions/2026-Q4/2026-10-02-postmark-403-smtp-fallback.md`).
- The block is per egress IP. On 2026-10-02 at 03:33 UTC decide was blocked while gen succeeded in the same minute. us-east4 Cloud Run Jobs were never blocked. From a residential IP, all 3 `api.postmarkapp.com` IPs answer normally.
- Postmark **IP Allowlisting** (UI-only, released 2026-08-27) is **OFF** at both account and server level (checked 2026-10-02). It also returns JSON `ErrorCode 1480` when it blocks, which is a different signature from this.
- Retrying the API is useless: each Cloud Run instance keeps the same egress IP, so the block lasts hours. v0.261.1's retries never got through. Postmark's own advice: retry only helps if the IP changes; otherwise use a dedicated static egress.

## How the current mitigation works

| | karaoke-backend | karaoke-decide |
|---|---|---|
| Code | `backend/services/email_service.py` → `PostmarkEmailProvider._send_via_smtp` | `backend/services/email_service.py` → `EmailService._send_via_smtp` |
| Version / PR | v0.263.1 (#1112), v0.263.2 (#1113) | v0.9.3 (decide #147) |
| Log field | `jsonPayload.message` (structured) | `textPayload` (plain) |

- **Falls back to SMTP on:** an HTML (non-JSON) 403, or the API being unreachable (connect errors).
- **Never falls back on:** Postmark JSON errors (bad token, invalid payload, suppressed recipient) or read timeouts (Postmark may already have accepted the message, so a resend could duplicate).
- **SMTP auth:** the server token is both username and password. The header `X-PM-Message-Stream: outbound` selects the stream.
- **Trade-offs while on SMTP:** no Postmark `MessageID` is returned (the email_log entry is not linked to Postmark), and suppressed or invalid recipients only show up later as bounces in Postmark, not as an immediate error.
- **Requirement:** SMTP must stay **enabled** on the Postmark server "Nomad Karaoke Gen" (ID 19037711) under Server → Settings.
- **Log lines to grep:** `falling back to SMTP` (block hit), `via Postmark SMTP fallback` (sent), `Failed to send email to … via Postmark SMTP fallback` (lost; this is what the alert counts).

## The alert (tripwire)

- Code: `infrastructure/modules/monitoring.py` → `create_email_delivery_observability()`.
- **Log-based metric:** `logging.googleapis.com/user/email/postmark_smtp_fallback_failures`.
- **Filter:** `SMTP_FALLBACK_FAILURE_LOG_FILTER`. It must match **both** `jsonPayload.message` (gen) and `textPayload` (decide).
  - Validated 2026-10-02: 0 matches, which is correct. A positive control on the gen JSON branch matched 12/12 real fallback sends; one on the decide text branch matched 5/5 real decide 403 lines.
- **Fires on:** any failure within a 10-minute window. Notifies the `alerts-email` channel.
- **Cost:** counter metric points are within the free allotment. Alert policies are free until **2027-09-01**, then **$0.35/month** per metric reference (Google Cloud Observability pricing, checked 2026-10-02).

## Options if we need more

Prices are from Google's Cloud NAT pricing page, checked 2026-10-02:
- **Gateway:** $0.0014/hr per instance using it, capped at $0.044/hr above 32 instances.
- **Static IP:** $0.005/hr each.
- **Data processing:** $0.045/GiB, counting both directions of connections *initiated* by the workload. Responses to inbound user requests do **not** go through the NAT.

Measured usage (2026-10-02):
- Average instances (7-day, hourly max): karaoke-backend ~2.2, karaoke-decide ~0.9 (it scales to zero ~half the time).
- The existing `github-runners-nat` cost **~$4.53/30 days** (IP $2.90 + gateway uptime $1.63).

### Option A (recommended if needed): reuse the existing Cloud NAT with a reserved static IP. ~$4–7/month extra

`infrastructure/compute/github_runners.py` already creates `github-runners-router` and `github-runners-nat` on the `default` network in us-central1, with `AUTO_ONLY` IPs and `ALL_SUBNETWORKS_ALL_IP_RANGES`.

Steps:
1. Reserve a static external IP: `compute.Address("email-egress-ip", region="us-central1")`.
2. Switch `github-runners-nat` to `nat_ip_allocate_option="MANUAL_ONLY"` with `nat_ips=[address.self_link]`. Its traffic is all ours (CI runners plus our services), so the IP's reputation is our own. You could also keep the runners on AUTO by giving the NAT subnetwork-specific config and putting Cloud Run on its own subnet; that's more config for no saving.
3. Put **karaoke-backend** and **karaoke-decide** on **Direct VPC egress**: `--network=default --subnet=default --vpc-egress=all-traffic`, set in each service's deploy config (gen: the CI deploy step or `modules/cloud_run.py`; decide: its own `infrastructure/`). `all-traffic` is required, because `private-ranges-only` keeps internet traffic on Google's shared IPs.
4. Make sure **Private Google Access** is on for that subnet, so Firestore/GCS/Secret Manager/Gemini traffic goes direct and isn't billed as NAT data processing.
5. **Size the NAT for Cloud Run.** Port exhaustion makes outbound connections time out, which would break email again and every other outbound call too. Settings on the `RouterNat`:
   - `endpoint_types=["ENDPOINT_TYPE_VM"]`. This is the default, and Direct VPC egress instances count as VM endpoints.
   - `enable_dynamic_port_allocation=True` with `min_ports_per_vm=128` and `max_ports_per_vm=4096`. Google's guidance is a minimum of at least ~2× the ports each Cloud Run instance needs. A backend instance makes many concurrent outbound calls (Stripe, Postmark, flacfetch, AudioShake), so 64 static ports could run out.
   - Capacity check: 1 NAT IP = 64,512 ports, about 504 endpoints at 128 min ports each. Peak demand is 20 backend + 10 decide max instances, plus CI runners, which is well under that. If NAT logs ever show `OUT_OF_RESOURCES` / dropped packets, add a second reserved IP to `nat_ips`.
   - Turn on NAT logging (`log_config.filter="ALL"` temporarily, then back to `ERRORS_ONLY`) for the first week, to watch for drops and to measure the data-processing volume.
6. Apply locally with `pulumi up` **before** merging (repo rule).
7. **Verify the reserved IP is actually used.** A successful Postmark send alone doesn't prove it, since the old dynamic IP might just not be blocked at that moment.
   - From inside the deployed service's network path, call an IP-echo endpoint, e.g. `curl -s https://checkip.amazonaws.com`. Do this from a one-off Cloud Run job using the same `--network/--subnet/--vpc-egress=all-traffic` settings, or a temporary admin-only debug endpoint. The result must equal the reserved `email-egress-ip` address.
   - With NAT logging on `ALL`, confirm that translations from the Cloud Run subnet show the reserved IP.
   - Then trigger a magic link and confirm `Email sent to … via Postmark` (the API path, not the SMTP fallback) in the logs.
   - Optionally tell Postmark the new IP on ticket #11562402.

Cost estimate:
- **IP:** switching AUTO → 1 reserved IP: $3.65/mo vs the ~$2.90 auto-IP spend today, so **+~$0.75**.
- **Gateway:** +(2.2 + 0.9) × $0.0014 × 730h = **+~$3.20**.
- **Data processing:** **+~$0.50–3**, unmeasured. It covers the outbound calls we make (Stripe, Postmark, flacfetch, AudioShake, Dropbox, YouTube, etc.). It could be more if the backend downloads large media from non-Google hosts, so measure it for a week via NAT logs after enabling.
- **Total: ~$4–7/month.**

### Option B: dedicated new NAT just for the Cloud Run services. ~$7–10/month extra

Same as A but with a new router + NAT + reserved IP scoped to one subnet. Cleaner separation from the CI runners, but you pay a second IP and gateway. Only worth it if CI-runner traffic ever risks our email IP's reputation.

### Option C: route only email through a fixed IP. ~$0 extra, more moving parts

Send Postmark traffic through something that already has a static IP, e.g. a tiny relay or proxy. The candidates are poor:
- The **encoding-worker VMs** have reserved IPs (34.57.78.246, 34.10.189.118, 34.16.73.140), but they're Spot and stop when idle, so they're unsuitable as an always-on relay.
- `divebar-sync` is TERMINATED.
- Home servers are out (reliability, and residential IPs have their own deliverability problems).

Not recommended unless we get an always-on box with a static IP for another reason.

### Option D: switch or add an email provider

As a last resort, send via a second provider (e.g. SES/Resend) when both Postmark paths fail. That means another account and another set of DNS/DKIM. Only consider it if Postmark keeps blocking us even from a static IP.

## Decision log

- **2026-10-02:** shipped the SMTP fallback (gen + decide) and the tripwire alert. **Deferred Option A to save money** (Andrew: "we're trying to save money everywhere right now"). Revisit if the alert fires, if SMTP-path bounces show something odd, or if we need Postmark `MessageID`s during blocks.
