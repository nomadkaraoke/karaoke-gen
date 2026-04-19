"""Tests for error_monitor.config module."""

import os
from unittest import mock

import pytest


class TestMonitoredServiceLists:
    """Tests that all monitored service lists have the expected entries."""

    def test_cloud_run_services_count(self):
        from backend.services.error_monitor import config

        # Gen2 Cloud Functions log as cloud_run_revision, so they live in
        # MONITORED_CLOUD_RUN_SERVICES alongside real Cloud Run services.
        assert len(config.MONITORED_CLOUD_RUN_SERVICES) >= 3

    def test_cloud_run_services_contains_expected(self):
        from backend.services.error_monitor import config

        assert "karaoke-backend" in config.MONITORED_CLOUD_RUN_SERVICES
        assert "karaoke-decide" in config.MONITORED_CLOUD_RUN_SERVICES
        assert "audio-separator" in config.MONITORED_CLOUD_RUN_SERVICES

    def test_cloud_run_jobs_count(self):
        from backend.services.error_monitor import config

        assert len(config.MONITORED_CLOUD_RUN_JOBS) == 4

    def test_cloud_run_jobs_contains_expected(self):
        from backend.services.error_monitor import config

        assert "video-encoding-job" in config.MONITORED_CLOUD_RUN_JOBS
        assert "lyrics-transcription-job" in config.MONITORED_CLOUD_RUN_JOBS
        assert "audio-separation-job" in config.MONITORED_CLOUD_RUN_JOBS
        assert "audio-download-job" in config.MONITORED_CLOUD_RUN_JOBS

    def test_gen2_cloud_functions_monitored_as_cloud_run_services(self):
        # Gen2 Cloud Functions log under resource.type=cloud_run_revision,
        # so they must appear in MONITORED_CLOUD_RUN_SERVICES, not
        # MONITORED_CLOUD_FUNCTIONS. See config.py comment.
        from backend.services.error_monitor import config

        expected = [
            "gdrive-validator",
            "github-runner-manager",
            "backup-to-aws",
            "divebar-mirror",
            "kn-data-sync",
            "divebar-lookup",
            "encoding-worker-idle-shutdown",
        ]
        for fn in expected:
            assert (
                fn in config.MONITORED_CLOUD_RUN_SERVICES
            ), f"Expected Gen2 function '{fn}' in MONITORED_CLOUD_RUN_SERVICES"

    def test_cloud_functions_list_empty_for_gen1_only(self):
        # MONITORED_CLOUD_FUNCTIONS is reserved for Gen1 Cloud Functions
        # (which log as resource.type=cloud_function). All current functions
        # are Gen2 and are handled via MONITORED_CLOUD_RUN_SERVICES.
        from backend.services.error_monitor import config

        assert config.MONITORED_CLOUD_FUNCTIONS == []

    def test_gce_instances_count_at_least_4(self):
        from backend.services.error_monitor import config

        assert len(config.MONITORED_GCE_INSTANCES) >= 4

    def test_gce_instances_contains_expected(self):
        from backend.services.error_monitor import config

        expected = [
            "encoding-worker-a",
            "encoding-worker-b",
            "flacfetch-vm",
            "divebar-sync-vm",
        ]
        for instance in expected:
            assert instance in config.MONITORED_GCE_INSTANCES, f"Expected '{instance}' in MONITORED_GCE_INSTANCES"


class TestDefaultValues:
    """Tests that config module exposes correct default constants."""

    def test_gcp_project_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            # Remove GCP_PROJECT if set, then re-import via reload
            env = {k: v for k, v in os.environ.items() if k != "GCP_PROJECT"}
            with mock.patch.dict(os.environ, env, clear=True):
                import importlib
                from backend.services.error_monitor import config as cfg
                importlib.reload(cfg)
                assert cfg.GCP_PROJECT == "nomadkaraoke"

    def test_lookback_minutes_default(self):
        from backend.services.error_monitor import config

        assert config.LOOKBACK_MINUTES == 15

    def test_max_log_entries_default(self):
        from backend.services.error_monitor import config

        assert config.MAX_LOG_ENTRIES == 500

    def test_spike_multiplier_default(self):
        from backend.services.error_monitor import config

        assert config.SPIKE_MULTIPLIER == 5.0

    def test_spike_min_count_default(self):
        from backend.services.error_monitor import config

        assert config.SPIKE_MIN_COUNT == 5

    def test_auto_resolve_multiplier_default(self):
        from backend.services.error_monitor import config

        assert config.AUTO_RESOLVE_MULTIPLIER == 8

    def test_max_discord_messages_per_run_default(self):
        from backend.services.error_monitor import config

        assert config.MAX_DISCORD_MESSAGES_PER_RUN == 10

    def test_discord_max_message_length_default(self):
        from backend.services.error_monitor import config

        assert config.DISCORD_MAX_MESSAGE_LENGTH == 2000

    def test_max_active_patterns_default(self):
        from backend.services.error_monitor import config

        assert config.MAX_ACTIVE_PATTERNS == 500

    def test_rolling_window_days_default(self):
        from backend.services.error_monitor import config

        assert config.ROLLING_WINDOW_DAYS == 7

    def test_max_normalized_message_length_default(self):
        from backend.services.error_monitor import config

        assert config.MAX_NORMALIZED_MESSAGE_LENGTH == 200

    def test_min_patterns_for_analysis_default(self):
        from backend.services.error_monitor import config

        assert isinstance(config.MIN_PATTERNS_FOR_ANALYSIS, int)
        assert config.MIN_PATTERNS_FOR_ANALYSIS >= 1

    def test_auto_resolve_min_hours(self):
        from backend.services.error_monitor import config

        assert config.AUTO_RESOLVE_MIN_HOURS == 6

    def test_auto_resolve_max_hours(self):
        from backend.services.error_monitor import config

        assert config.AUTO_RESOLVE_MAX_HOURS == 168  # 1 week

    def test_auto_resolve_fallback_hours(self):
        from backend.services.error_monitor import config

        assert config.AUTO_RESOLVE_FALLBACK_HOURS == 48

    def test_service_dependency_map_is_string(self):
        from backend.services.error_monitor import config

        assert isinstance(config.SERVICE_DEPENDENCY_MAP, str)
        assert len(config.SERVICE_DEPENDENCY_MAP) > 0
        assert "karaoke-backend" in config.SERVICE_DEPENDENCY_MAP

    def test_llm_analysis_model_is_string(self):
        from backend.services.error_monitor import config

        assert isinstance(config.LLM_ANALYSIS_MODEL, str)
        assert len(config.LLM_ANALYSIS_MODEL) > 0

    def test_llm_vertex_location_is_string(self):
        from backend.services.error_monitor import config

        assert isinstance(config.LLM_VERTEX_LOCATION, str)
        assert len(config.LLM_VERTEX_LOCATION) > 0


class TestEnvVarOverrides:
    """Tests that environment variable overrides work correctly."""

    def test_llm_analysis_enabled_default_is_false(self):
        """LLM_ANALYSIS_ENABLED should default to False when env var is not set."""
        import importlib
        env = {k: v for k, v in os.environ.items() if k != "LLM_ANALYSIS_ENABLED"}
        with mock.patch.dict(os.environ, env, clear=True):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.get_llm_enabled() is False

    def test_llm_analysis_enabled_true_when_env_var_set(self):
        """get_llm_enabled() should return True when LLM_ANALYSIS_ENABLED=true."""
        import importlib
        with mock.patch.dict(os.environ, {"LLM_ANALYSIS_ENABLED": "true"}):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.get_llm_enabled() is True

    def test_llm_analysis_enabled_true_when_env_var_set_uppercase(self):
        """get_llm_enabled() should handle case-insensitive 'TRUE'."""
        import importlib
        with mock.patch.dict(os.environ, {"LLM_ANALYSIS_ENABLED": "TRUE"}):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.get_llm_enabled() is True

    def test_llm_analysis_enabled_false_when_env_var_set_false(self):
        """get_llm_enabled() should return False when LLM_ANALYSIS_ENABLED=false."""
        import importlib
        with mock.patch.dict(os.environ, {"LLM_ANALYSIS_ENABLED": "false"}):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.get_llm_enabled() is False

    def test_gcp_project_overridable_via_env(self):
        """GCP_PROJECT should pick up the GCP_PROJECT environment variable."""
        import importlib
        with mock.patch.dict(os.environ, {"GCP_PROJECT": "my-test-project"}):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.GCP_PROJECT == "my-test-project"

    def test_gcp_region_has_default(self):
        from backend.services.error_monitor import config

        assert isinstance(config.GCP_REGION, str)
        assert len(config.GCP_REGION) > 0

    def test_gcp_region_overridable_via_env(self):
        """GCP_REGION should pick up the GCP_REGION environment variable."""
        import importlib
        with mock.patch.dict(os.environ, {"GCP_REGION": "europe-west1"}):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.GCP_REGION == "europe-west1"


class TestHelperFunctions:
    """Tests for config helper functions."""

    def test_get_discord_webhook_secret_name_returns_string(self):
        from backend.services.error_monitor import config

        result = config.get_discord_webhook_secret_name()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_digest_mode_returns_bool(self):
        from backend.services.error_monitor import config

        result = config.get_digest_mode()
        assert isinstance(result, bool)

    def test_get_digest_mode_default_false(self):
        """DIGEST_MODE should default to False."""
        import importlib
        env = {k: v for k, v in os.environ.items() if k != "DIGEST_MODE"}
        with mock.patch.dict(os.environ, env, clear=True):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.get_digest_mode() is False

    def test_get_digest_mode_true_when_env_set(self):
        """get_digest_mode() should return True when DIGEST_MODE=true."""
        import importlib
        with mock.patch.dict(os.environ, {"DIGEST_MODE": "true"}):
            from backend.services.error_monitor import config as cfg
            importlib.reload(cfg)
            assert cfg.get_digest_mode() is True
