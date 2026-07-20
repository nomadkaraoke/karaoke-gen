"""
Cloud Monitoring resources.

Manages alert policies for proactive monitoring and incident response. Every
alert policy attaches the notification channel(s) created by
``create_notification_channels`` — without that, a firing alert resolves into
the void (verified on 2026-05-17: three ephemeral-dispatcher create failures
fired the create-failures alert correctly, but the project had zero
notification channels attached, so nobody got paged for 14 hours).
"""

import pulumi
import pulumi_gcp as gcp

from config import PROJECT_ID


# Email address that receives all monitoring alerts. Cloud Monitoring delivers
# a one-time confirmation link to this address when the channel is created;
# the channel stays in PENDING state until verified.
_ALERT_EMAIL = "andrew@beveridge.uk"


def create_notification_channels() -> dict[str, gcp.monitoring.NotificationChannel]:
    """Create the notification channels attached to every alert policy.

    Returns a dict so callers can pass selected channels to specific policies
    in the future (e.g. critical-only routing). Today every policy uses
    ``channels["email"]``.

    Swapping email for Discord later: Discord supports Slack-compatible
    webhooks at ``<discord_webhook_url>/slack``. Add a second channel with
    ``type_="slack"`` and ``sensitive_labels={"auth_token": <url>}``; reference
    it in the policies that should page Discord. Keep email as the durable
    fallback.
    """
    channels: dict[str, gcp.monitoring.NotificationChannel] = {}
    channels["email"] = gcp.monitoring.NotificationChannel(
        "alerts-email",
        display_name="Andrew (email)",
        type="email",
        labels={"email_address": _ALERT_EMAIL},
        description="Primary alert channel for Cloud Monitoring policies.",
        enabled=True,
    )
    return channels


def _channel_names(channels: dict[str, gcp.monitoring.NotificationChannel]) -> list[pulumi.Output[str]]:
    """Return the .name Outputs of every channel (the form AlertPolicy wants)."""
    return [ch.name for ch in channels.values()]


def create_alert_policies(
    channels: dict[str, gcp.monitoring.NotificationChannel],
) -> dict[str, gcp.monitoring.AlertPolicy]:
    """
    Create all Cloud Monitoring alert policies.

    Args:
        channels: Notification channels to attach (from
            :func:`create_notification_channels`). All policies get every
            channel.

    Returns:
        dict: Dictionary mapping alert names to AlertPolicy resources.
    """
    notification_channels = _channel_names(channels)
    alerts = {}

    # Alert: Sustained server errors (5xx).
    #
    # NOTE on semantics: with ALIGN_RATE + REDUCE_SUM the threshold is a request
    # *rate* (requests/second), NOT a true percentage — the old "Error rate >
    # 10%" name was a misnomer. It also matched ALL non-2xx codes, so a burst of
    # 404s from an internet vuln-scanner (2026-07-20: 0.167 req/s of 404s from a
    # single IP hammering /.env, /.ssh/... ) tripped it despite the service being
    # perfectly healthy.
    #
    # Fix: scope to `response_code_class="5xx"` so only genuine *server* errors
    # count — client/bot 404s never page us. WAF/rate-limiting at the Cloudflare
    # edge (see modules/edge_security.py) is the primary defense that keeps that
    # scan traffic off the metric entirely. Threshold = >0.1 5xx req/s sustained
    # over 5 min. With COMPARISON_GT the boundary is strict: 6 errors/min
    # (=0.1/s) does NOT fire; >=7/min sustained for 5 min does → real regression.
    alerts["error_rate"] = gcp.monitoring.AlertPolicy(
        "high-error-rate-alert",
        display_name="Karaoke Backend - High Server-Error Rate (5xx)",
        combiner="OR",
        conditions=[
            gcp.monitoring.AlertPolicyConditionArgs(
                display_name="Sustained 5xx server errors > 0.1/s (5 min)",
                condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                    filter='resource.type="cloud_run_revision" AND resource.labels.service_name="karaoke-backend" AND metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"',
                    comparison="COMPARISON_GT",
                    threshold_value=0.1,
                    duration="300s",
                    aggregations=[
                        gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                            alignment_period="60s",
                            per_series_aligner="ALIGN_RATE",
                            cross_series_reducer="REDUCE_SUM",
                        ),
                    ],
                    trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                        count=1,
                    ),
                ),
            ),
        ],
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            auto_close="3600s",
        ),
        documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
            content="Sustained 5xx server errors on karaoke-backend (not client/bot 404s). Check Cloud Run logs for crashes, timeouts, or dependency failures.\n\nDashboard: https://console.cloud.google.com/run/detail/us-central1/karaoke-backend/logs?project=nomadkaraoke",
            mime_type="text/markdown",
        ),
        enabled=True,
        notification_channels=notification_channels,
    )

    # Alert: Cloud Tasks Queue Backlog (tasks piling up)
    alerts["queue_backlog"] = gcp.monitoring.AlertPolicy(
        "queue-backlog-alert",
        display_name="Karaoke Backend - Queue Backlog",
        combiner="OR",
        conditions=[
            gcp.monitoring.AlertPolicyConditionArgs(
                display_name="Queue depth > 50 tasks",
                condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                    filter='resource.type="cloud_tasks_queue" AND metric.type="cloudtasks.googleapis.com/queue/depth"',
                    comparison="COMPARISON_GT",
                    threshold_value=50,
                    duration="600s",
                    aggregations=[
                        gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                            alignment_period="60s",
                            per_series_aligner="ALIGN_MEAN",
                            cross_series_reducer="REDUCE_MAX",
                        ),
                    ],
                    trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                        count=1,
                    ),
                ),
            ),
        ],
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            auto_close="1800s",
        ),
        documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
            content="Cloud Tasks queue backlog detected. Tasks are piling up faster than workers can process.\n\nPossible causes:\n- Worker errors (check logs)\n- External API issues (Modal, AudioShake)\n- Cloud Run scaling limits\n\nDashboard: https://console.cloud.google.com/cloudtasks?project=nomadkaraoke",
            mime_type="text/markdown",
        ),
        enabled=True,
        notification_channels=notification_channels,
    )

    # Alert: High Memory Utilization (workers may be crashing)
    alerts["memory"] = gcp.monitoring.AlertPolicy(
        "high-memory-alert",
        display_name="Karaoke Backend - High Memory Usage",
        combiner="OR",
        conditions=[
            gcp.monitoring.AlertPolicyConditionArgs(
                display_name="Memory > 85%",
                condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                    filter='resource.type="cloud_run_revision" AND resource.labels.service_name="karaoke-backend" AND metric.type="run.googleapis.com/container/memory/utilizations"',
                    comparison="COMPARISON_GT",
                    threshold_value=0.85,
                    duration="300s",
                    aggregations=[
                        gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                            alignment_period="60s",
                            per_series_aligner="ALIGN_PERCENTILE_95",
                            cross_series_reducer="REDUCE_MAX",
                        ),
                    ],
                    trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                        count=1,
                    ),
                ),
            ),
        ],
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            auto_close="1800s",
        ),
        documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
            content="High memory utilization on karaoke-backend. Workers may be running out of memory.\n\nActions:\n- Check for memory leaks in worker code\n- Consider increasing Cloud Run memory limits\n- Review temp file cleanup",
            mime_type="text/markdown",
        ),
        enabled=True,
        notification_channels=notification_channels,
    )

    # Alert: Cloud Run Service Unavailable
    alerts["service_unavailable"] = gcp.monitoring.AlertPolicy(
        "service-unavailable-alert",
        display_name="Karaoke Backend - Service Unavailable",
        combiner="OR",
        conditions=[
            gcp.monitoring.AlertPolicyConditionArgs(
                display_name="No healthy instances",
                condition_absent=gcp.monitoring.AlertPolicyConditionConditionAbsentArgs(
                    filter='resource.type="cloud_run_revision" AND resource.labels.service_name="karaoke-backend" AND metric.type="run.googleapis.com/container/instance_count"',
                    duration="300s",
                    aggregations=[
                        gcp.monitoring.AlertPolicyConditionConditionAbsentAggregationArgs(
                            alignment_period="60s",
                            per_series_aligner="ALIGN_MEAN",
                            cross_series_reducer="REDUCE_SUM",
                        ),
                    ],
                ),
            ),
        ],
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            auto_close="1800s",
        ),
        documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
            content="Karaoke backend service appears to be down - no healthy Cloud Run instances detected.\n\nImmediate actions:\n1. Check Cloud Run logs for crash reasons\n2. Verify recent deployments\n3. Check external dependencies (Modal, AudioShake)\n\nService URL: https://api.nomadkaraoke.com/api/health",
            mime_type="text/markdown",
        ),
        enabled=True,
        notification_channels=notification_channels,
    )

    return alerts


def create_ephemeral_runner_observability(
    channels: dict[str, gcp.monitoring.NotificationChannel],
) -> dict:
    """Log-based metrics + alerts for the ephemeral GHA runner dispatcher.

    Adds two alerts that are useful during the Phase 3 soak and afterward:

    1. Dispatcher VM-create failures (the dispatcher returns 503 + logs
       ``create_ephemeral_runner failed`` — any occurrence in 10 min fires).
    2. Long-running ephemeral runner VMs (uptime > 2h30m on instances labelled
       ``purpose=gha-ephemeral-runner`` — catches hung jobs or cleanup bugs).
    """
    notification_channels = _channel_names(channels)
    resources: dict = {}

    # ---- Log-based metric: dispatch failures -------------------------------
    resources["dispatch_failure_metric"] = gcp.logging.Metric(
        "runner-dispatcher-failures",
        name="runner_dispatcher/create_failures",
        description="Counts create_ephemeral_runner failures (any zone)",
        # Function logs include the function name; runner-manager covers both
        # legacy + ephemeral entry points.
        filter=(
            'resource.type="cloud_run_revision" '
            'AND resource.labels.service_name="github-runner-manager" '
            'AND textPayload:"create_ephemeral_runner failed"'
        ),
        metric_descriptor=gcp.logging.MetricMetricDescriptorArgs(
            metric_kind="DELTA",
            value_type="INT64",
            unit="1",
            display_name="Runner dispatcher create failures",
        ),
    )

    # ---- Log-based metric: orphan kills (visibility, not alerting) ---------
    resources["orphan_kill_metric"] = gcp.logging.Metric(
        "runner-dispatcher-orphan-kills",
        name="runner_dispatcher/orphan_kills",
        description="Counts orphan VMs deleted by the dispatcher cleanup pass",
        filter=(
            'resource.type="cloud_run_revision" '
            'AND resource.labels.service_name="github-runner-manager" '
            'AND textPayload:"age=" AND textPayload:"deleting"'
        ),
        metric_descriptor=gcp.logging.MetricMetricDescriptorArgs(
            metric_kind="DELTA",
            value_type="INT64",
            unit="1",
            display_name="Runner dispatcher orphan-VM deletions",
        ),
    )

    # ---- Alert: any dispatcher create failure in last 10 minutes -----------
    resources["dispatch_failure_alert"] = gcp.monitoring.AlertPolicy(
        "runner-dispatcher-failures-alert",
        display_name="GHA Runner Dispatcher - Create Failures",
        combiner="OR",
        conditions=[
            gcp.monitoring.AlertPolicyConditionArgs(
                display_name="Any create_ephemeral_runner failure",
                condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                    filter=(
                        'metric.type="logging.googleapis.com/user/runner_dispatcher/create_failures" '
                        'AND resource.type="cloud_run_revision"'
                    ),
                    comparison="COMPARISON_GT",
                    threshold_value=0,
                    duration="0s",
                    aggregations=[
                        gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                            alignment_period="600s",
                            per_series_aligner="ALIGN_SUM",
                            cross_series_reducer="REDUCE_SUM",
                        ),
                    ],
                    trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                        count=1,
                    ),
                ),
            ),
        ],
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            auto_close="3600s",
        ),
        documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
            content=(
                "Ephemeral runner dispatcher failed to create a VM.\n\n"
                "Likely causes:\n"
                "- GCE quota exhausted (CPU or NVIDIA_T4_GPUS in us-central1)\n"
                "- GitHub JIT mint API rate-limited or PAT expired\n"
                "- Image family deprecated/missing (gha-runner-{general,build,gpu})\n"
                "- Both primary and fallback zones exhausted\n\n"
                "Logs: filter `resource.labels.service_name=\"github-runner-manager\"` "
                "in Cloud Logging for `create_ephemeral_runner failed`.\n\n"
                "Rollback: not possible from this alert — Phase 4 deleted the "
                "legacy long-lived VMs (PR #780). Recovery requires either "
                "fixing the failure mode or recreating the legacy pool from "
                "git history."
            ),
            mime_type="text/markdown",
        ),
        enabled=True,
        notification_channels=notification_channels,
        opts=pulumi.ResourceOptions(depends_on=[resources["dispatch_failure_metric"]]),
    )

    # ---- Alert: ephemeral VM uptime > 2h30m --------------------------------
    # `compute.googleapis.com/instance/uptime_total` is a cumulative count of
    # seconds the VM has been up. The orphan-cleanup pass kills VMs at
    # MAX_VM_LIFETIME_MINUTES=120 on a 15-min scheduler tick, so worst-case
    # lifetime is ~135min. ALIGN_MAX over a 5-min window plus the 300s
    # duration means a threshold of exactly 7200s fires on every normal
    # cleanup (the window containing the pre-delete sample still reports
    # max>threshold for 5min after the VM is gone). Threshold set to 9000s
    # (150min = worst-case + 15min buffer) so this alert only fires when
    # cleanup is genuinely broken, not on routine cleanups that land within
    # the expected window.
    resources["long_lived_vm_alert"] = gcp.monitoring.AlertPolicy(
        "runner-dispatcher-long-lived-vm-alert",
        display_name="GHA Runner Dispatcher - VM Alive > 2h30m",
        combiner="OR",
        conditions=[
            gcp.monitoring.AlertPolicyConditionArgs(
                display_name="Ephemeral runner VM uptime > 2h30m",
                condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                    filter=(
                        'metric.type="compute.googleapis.com/instance/uptime_total" '
                        'AND resource.type="gce_instance" '
                        'AND metadata.user_labels.purpose="gha-ephemeral-runner"'
                    ),
                    comparison="COMPARISON_GT",
                    threshold_value=9000,
                    duration="300s",
                    aggregations=[
                        gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                            alignment_period="300s",
                            per_series_aligner="ALIGN_MAX",
                            cross_series_reducer="REDUCE_MAX",
                            group_by_fields=["resource.labels.instance_id"],
                        ),
                    ],
                    trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                        count=1,
                    ),
                ),
            ),
        ],
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            # GCP minimum is 30 minutes — anything lower returns 400.
            auto_close="1800s",
        ),
        documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
            content=(
                "An ephemeral GHA runner VM has been alive for more than 2h30m.\n"
                "Orphan-cleanup should kill VMs at MAX_VM_LIFETIME_MINUTES (120)\n"
                "on a 15-min scheduler tick — worst-case lifetime is ~135min.\n"
                "If this alert fires, the cleanup pass is broken or the VM is\n"
                "escaping it.\n\n"
                "Investigate:\n"
                "1. `gcloud compute instances list --filter=labels.purpose=gha-ephemeral-runner`\n"
                "2. Check Cloud Function logs for the cleanup pass\n"
                "3. Manually delete: `gcloud compute instances delete <name> --zone=<zone>`"
            ),
            mime_type="text/markdown",
        ),
        enabled=True,
        notification_channels=notification_channels,
    )

    return resources
