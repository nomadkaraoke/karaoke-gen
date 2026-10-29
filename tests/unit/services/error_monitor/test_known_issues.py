"""Tests for error_monitor.known_issues module."""

import pytest


class TestIgnoreReasonDataclass:
    """IgnoreReason dataclass should hold pattern_name and reason fields."""

    def test_ignore_reason_has_pattern_name(self):
        from backend.services.error_monitor.known_issues import IgnoreReason

        ir = IgnoreReason(pattern_name="startup_probe", reason="Cloud Run cold start")
        assert ir.pattern_name == "startup_probe"

    def test_ignore_reason_has_reason(self):
        from backend.services.error_monitor.known_issues import IgnoreReason

        ir = IgnoreReason(pattern_name="startup_probe", reason="Cloud Run cold start")
        assert ir.reason == "Cloud Run cold start"

    def test_ignore_reason_is_dataclass_equality(self):
        from backend.services.error_monitor.known_issues import IgnoreReason

        a = IgnoreReason(pattern_name="foo", reason="bar")
        b = IgnoreReason(pattern_name="foo", reason="bar")
        assert a == b

    def test_ignore_reason_inequality(self):
        from backend.services.error_monitor.known_issues import IgnoreReason

        a = IgnoreReason(pattern_name="foo", reason="bar")
        b = IgnoreReason(pattern_name="baz", reason="bar")
        assert a != b


class TestStartupProbePattern:
    """startup_probe — 'startup probe failed' should be ignored (Cloud Run cold start)."""

    def test_startup_probe_exact(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "startup probe failed")
        assert result is not None
        assert result.pattern_name == "startup_probe"

    def test_default_startup_tcp_probe(self):
        """Cloud Run's own wording names the probe type (seen 2026-10-20)."""
        from backend.services.error_monitor.known_issues import should_ignore

        msg = (
            'Default STARTUP TCP probe failed 1 time consecutively for container '
            '"karaoke-backend-1" on port 8080. The instance was not started.'
        )
        result = should_ignore("karaoke-backend", msg)
        assert result is not None
        assert result.pattern_name == "startup_probe"

    def test_startup_probe_uppercase(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "Startup Probe Failed")
        assert result is not None
        assert result.pattern_name == "startup_probe"

    def test_startup_probe_in_longer_message(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            "Readiness probe failed: startup probe failed for container api",
        )
        assert result is not None
        assert result.pattern_name == "startup_probe"

    def test_startup_probe_any_service(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("audio-separator", "startup probe failed")
        assert result is not None
        assert result.pattern_name == "startup_probe"


class TestReadyConditionPattern:
    """ready_condition — 'ready condition status changed' should be ignored (deploy event)."""

    def test_ready_condition_exact(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "ready condition status changed")
        assert result is not None
        assert result.pattern_name == "ready_condition"

    def test_ready_condition_uppercase(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "Ready Condition Status Changed")
        assert result is not None
        assert result.pattern_name == "ready_condition"

    def test_ready_condition_with_context(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            "Service karaoke-backend: ready condition status changed to True",
        )
        assert result is not None
        assert result.pattern_name == "ready_condition"


class TestSpotPreemptionPattern:
    """spot_preemption — 'instance was preempted' should be ignored (GitHub runner spot VMs)."""

    def test_spot_preemption_exact(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("encoding-worker-a", "instance was preempted")
        assert result is not None
        assert result.pattern_name == "spot_preemption"

    def test_spot_preemption_uppercase(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("encoding-worker-a", "Instance Was Preempted")
        assert result is not None
        assert result.pattern_name == "spot_preemption"

    def test_spot_preemption_in_sentence(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "encoding-worker-b",
            "GCE: instance was preempted, shutting down",
        )
        assert result is not None
        assert result.pattern_name == "spot_preemption"


class TestIdleShutdownPattern:
    """idle_shutdown — 'stopping idle encoding worker' should be ignored (expected behavior)."""

    def test_idle_shutdown_exact(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("encoding_worker_idle", "stopping idle encoding worker")
        assert result is not None
        assert result.pattern_name == "idle_shutdown"

    def test_idle_shutdown_uppercase(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("encoding_worker_idle", "Stopping Idle Encoding Worker")
        assert result is not None
        assert result.pattern_name == "idle_shutdown"

    def test_idle_shutdown_with_context(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "encoding_worker_idle",
            "stopping idle encoding worker after 30 minutes of inactivity",
        )
        assert result is not None
        assert result.pattern_name == "idle_shutdown"


class TestHealthCheck404Pattern:
    """health_check_404 — GET/HEAD /health with 404/not found should be ignored (load balancer)."""

    def test_health_check_get_404(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", 'GET /health 404')
        assert result is not None
        assert result.pattern_name == "health_check_404"

    def test_health_check_head_404(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", 'HEAD /health 404')
        assert result is not None
        assert result.pattern_name == "health_check_404"

    def test_health_check_get_not_found(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", 'GET /health not found')
        assert result is not None
        assert result.pattern_name == "health_check_404"

    def test_health_check_head_not_found(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", 'HEAD /health not found')
        assert result is not None
        assert result.pattern_name == "health_check_404"

    def test_health_check_uppercase_method(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "GET /health 404 Not Found")
        assert result is not None
        assert result.pattern_name == "health_check_404"

    def test_health_check_in_log_line(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            '10.0.0.1 - - [01/Jan/2024] "GET /health HTTP/1.1" 404 0',
        )
        assert result is not None
        assert result.pattern_name == "health_check_404"

    def test_non_health_404_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # A 404 for a real endpoint should NOT be ignored
        result = should_ignore("karaoke-backend", "GET /api/jobs/abc 404")
        assert result is None

    def test_health_200_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # /health returning 200 is not an error, but should still not trigger this pattern
        result = should_ignore("karaoke-backend", "GET /health 200")
        assert result is None


class TestSchedulerRetryPattern:
    """scheduler_retry — 'cloud scheduler.*retry' should be ignored (transient)."""

    def test_scheduler_retry_basic(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("runner_manager", "cloud scheduler retry attempt 1")
        assert result is not None
        assert result.pattern_name == "scheduler_retry"

    def test_scheduler_retry_uppercase(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("runner_manager", "Cloud Scheduler Retry")
        assert result is not None
        assert result.pattern_name == "scheduler_retry"

    def test_scheduler_retry_with_details(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "runner_manager",
            "cloud scheduler job failed, will retry in 60s",
        )
        assert result is not None
        assert result.pattern_name == "scheduler_retry"

    def test_cloud_scheduler_without_retry_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # "cloud scheduler" alone (without "retry") should NOT be suppressed
        result = should_ignore("runner_manager", "cloud scheduler job executed successfully")
        assert result is None


class TestRunnerStartupPattern:
    """runner_startup — 'github.runner.*(starting|stopping|idle)' should be ignored (lifecycle)."""

    def test_runner_starting(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("runner_manager", "github.runner.001 starting")
        assert result is not None
        assert result.pattern_name == "runner_startup"

    def test_runner_stopping(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("runner_manager", "github.runner.abc stopping")
        assert result is not None
        assert result.pattern_name == "runner_startup"

    def test_runner_idle(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("runner_manager", "github.runner.xyz idle")
        assert result is not None
        assert result.pattern_name == "runner_startup"

    def test_runner_starting_uppercase(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("runner_manager", "GitHub.Runner.001 Starting")
        assert result is not None
        assert result.pattern_name == "runner_startup"

    def test_runner_other_state_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # A GitHub runner in an unexpected state should NOT be ignored
        result = should_ignore("runner_manager", "github.runner.001 crashed")
        assert result is None


class TestContainerShutdownPattern:
    """container_shutdown — 'container called exit(0)' should be ignored (clean shutdown)."""

    def test_container_exit_0(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "container called exit(0)")
        assert result is not None
        assert result.pattern_name == "container_shutdown"

    def test_container_exit_0_uppercase(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "Container Called Exit(0)")
        assert result is not None
        assert result.pattern_name == "container_shutdown"

    def test_container_exit_0_in_sentence(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            "Cloud Run instance terminated: container called exit(0)",
        )
        assert result is not None
        assert result.pattern_name == "container_shutdown"

    def test_container_exit_nonzero_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # exit(1) = error, should NOT be ignored
        result = should_ignore("karaoke-backend", "container called exit(1)")
        assert result is None

    def test_container_exit_2_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "container called exit(2)")
        assert result is None


class TestCloudTraceExportPattern:
    """OTel Cloud Trace exporter failures are best-effort telemetry noise (2026-10-23)."""

    def test_cloud_trace_export_error(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "Error while writing to Cloud Trace")
        assert result is not None
        assert result.pattern_name == "cloud_trace_export"

    def test_cloud_trace_export_error_with_grpc_detail(self):
        from backend.services.error_monitor.known_issues import should_ignore

        msg = (
            "Error while writing to Cloud Trace\ngoogle.api_core.exceptions."
            "InternalServerError: 500 Internal error encountered."
        )
        result = should_ignore("karaoke-backend", msg)
        assert result is not None
        assert result.pattern_name == "cloud_trace_export"

    def test_other_trace_errors_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        assert should_ignore("karaoke-backend", "Failed to write job trace to Firestore") is None


class TestRealErrorsNotIgnored:
    """Real errors should never be silently ignored."""

    def test_firestore_deadline_exceeded(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            "Firestore transaction failed: DEADLINE_EXCEEDED",
        )
        assert result is None

    def test_audio_separation_oom(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "audio-separator",
            "CUDA out of memory. Tried to allocate 2.00 GiB",
        )
        assert result is None

    def test_payment_stripe_error(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            "Stripe charge failed: card_declined",
        )
        assert result is None

    def test_gcs_permission_denied(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            "Permission denied accessing gs://nomadkaraoke-prod/output.mp4",
        )
        assert result is None

    def test_worker_timeout(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "video-encoding-job",
            "Worker timed out after 3600 seconds",
        )
        assert result is None

    def test_unhandled_exception(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore(
            "karaoke-backend",
            "Unhandled exception: KeyError 'job_id'",
        )
        assert result is None


class TestUnknownServiceMessages:
    """Messages from unlisted/unknown services should use pattern matching, not service-gating."""

    def test_unknown_service_known_pattern_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # The patterns apply regardless of service name
        result = should_ignore("some-unknown-service", "startup probe failed")
        assert result is not None

    def test_unknown_service_real_error_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("some-unknown-service", "unhandled exception in request handler")
        assert result is None

    def test_empty_service_name_with_known_pattern(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("", "startup probe failed")
        assert result is not None

    def test_empty_message_not_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "")
        assert result is None


class TestEncodingWorkerCapacityRetry:
    """The transient GCE capacity/503 VM-start failure is parked for auto-retry
    by the render worker, so it must not page — it's known transient noise."""

    _MSG = (
        "[job:preview_ae5de3e5_3c4cdb053cbf] Encoding worker start failed, "
        "aborting retries: VM encoding-worker-fallback-b start failed in "
        "us-central1-b: 503 — SERVICE UNAVAILABLE"
    )

    def test_capacity_503_is_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", self._MSG)
        assert result is not None
        assert result.pattern_name == "encoding_worker_capacity_retry"

    def test_capacity_ignored_regardless_of_job_id_shape(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # plain job id form (the pre-existing May pattern) also suppressed
        msg = (
            "[job:1ea7effb] Encoding worker start failed, aborting retries: "
            "VM encoding-worker-fallback-a start failed in us-central1-a: "
            "503 — SERVICE UNAVAILABLE"
        )
        assert should_ignore("karaoke-backend", msg) is not None

    def test_capacity_code_variant_is_ignored(self):
        from backend.services.error_monitor.known_issues import should_ignore

        msg = (
            "[job:1ea7effb] Encoding worker start failed, aborting retries: "
            "VM encoding-worker-fallback-a could not be started in us-central1-a: "
            "ZONE_RESOURCE_POOL_EXHAUSTED — no resources available"
        )
        assert should_ignore("karaoke-backend", msg) is not None

    def test_genuine_job_failure_still_alerts(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # A real hard failure uses different wording and must NOT be suppressed.
        assert should_ignore(
            "karaoke-backend",
            "[job:1ea7effb] Video render failed: encoder crashed",
        ) is None

    def test_non_transient_start_failure_still_alerts(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # Same "aborting retries" prefix, but a non-transient GCE code — this is
        # "something is actually broken" and must NOT be suppressed.
        msg = (
            "[job:1ea7effb] Encoding worker start failed, aborting retries: "
            "VM encoding-worker-fallback-b start failed in us-central1-b: "
            "PERMISSION_DENIED — the caller does not have permission"
        )
        assert should_ignore("karaoke-backend", msg) is None

    def test_non_transient_with_503_in_message_body_still_alerts(self):
        from backend.services.error_monitor.known_issues import should_ignore

        # The code field is PERMISSION_DENIED (non-transient); "503" only appears
        # later in the free-text message. Must NOT be suppressed — the transient
        # signal is bound to the code position, not "anywhere in the line".
        msg = (
            "[job:1ea7effb] Encoding worker start failed, aborting retries: "
            "VM encoding-worker-fallback-b start failed in us-central1-b: "
            "PERMISSION_DENIED — prior attempt returned 503 SERVICE UNAVAILABLE"
        )
        assert should_ignore("karaoke-backend", msg) is None


class TestCaseInsensitiveMatching:
    """All patterns should match case-insensitively."""

    def test_startup_probe_all_caps(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "STARTUP PROBE FAILED")
        assert result is not None

    def test_ready_condition_mixed_case(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "Ready CONDITION Status CHANGED")
        assert result is not None

    def test_idle_shutdown_mixed_case(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("encoding_worker_idle", "STOPPING idle ENCODING worker")
        assert result is not None

    def test_container_shutdown_mixed_case(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("karaoke-backend", "Container Called EXIT(0)")
        assert result is not None


class TestRunnerManagerNoAvailableInstance:
    """github-runner-manager's Cloud Run 'no available instance' abort is a burst
    during runner scale-up; the runners still dispatch (seen 09-20, 09-23, 09-27,
    10-08), so it must not page."""

    MSG = (
        "The request was aborted because there was no available instance. "
        "Additional troubleshooting documentation can be found at: "
        "https://cloud.google.com/run/docs/troubleshooting#abort-request"
    )

    def test_ignored_for_runner_manager(self):
        from backend.services.error_monitor.known_issues import should_ignore

        result = should_ignore("github-runner-manager", self.MSG)
        assert result is not None
        assert result.pattern_name == "runner_manager_no_available_instance"

    def test_still_alerts_for_other_services(self):
        # On the customer-facing backend the same abort means dropped requests.
        from backend.services.error_monitor.known_issues import should_ignore

        assert should_ignore("karaoke-backend", self.MSG) is None

    def test_other_runner_manager_errors_still_alert(self):
        from backend.services.error_monitor.known_issues import should_ignore

        assert should_ignore("github-runner-manager", "Failed to create runner VM: quota exceeded") is None
