"""
Unit tests for tenant-aware job defaults and distribution overrides.

Tests that:
- Tenant config fields (brand_prefix, dropbox_path, gdrive_folder_id) are applied to jobs
- All tenant jobs are forced private
- Locked theme overrides user selection
- Private tenant jobs use tenant-specific paths (not NonPublished)
- Non-tenant private jobs still use NonPublished paths
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.models.tenant import TenantConfig, TenantDefaults, TenantFeatures, TenantBranding, TenantAuth
from backend.services.job_defaults_service import (
    get_effective_distribution_settings,
    get_effective_distribution_for_job,
    EffectiveDistributionSettings,
)


def _make_tenant_config(**defaults_kwargs) -> TenantConfig:
    """Create a TenantConfig with custom defaults for testing."""
    return TenantConfig(
        id="vocalstar",
        name="Vocal Star",
        subdomain="vocalstar.nomadkaraoke.com",
        defaults=TenantDefaults(
            locked_theme="vocalstar",
            distribution_mode="cloud_only",
            brand_prefix="VSTAR",
            dropbox_path="/Karaoke/Vocal-Star",
            gdrive_folder_id="gdrive-folder-123",
            **defaults_kwargs,
        ),
    )


def _make_job_mock(**kwargs):
    """Create a mock job object with the given attributes."""
    defaults = {
        "is_private": False,
        "tenant_id": None,
        "dropbox_path": None,
        "gdrive_folder_id": None,
        "brand_prefix": None,
        "enable_youtube_upload": False,
        "discord_webhook_url": None,
        "youtube_description_template": None,
    }
    defaults.update(kwargs)
    job = MagicMock()
    for key, value in defaults.items():
        setattr(job, key, value)
    return job


class TestSelectMixedAudioFiles:
    """Regression tests for _select_mixed_audio_files() in file_upload.py.

    The existing-instrumental file shares the uploads/{job}/audio/ directory with the
    mixed audio, so the 'audio/' prefix matches both. Before the fix, files[0] resolved to
    'existing_instrumental.mp3' (it sorts first) and transcription ran on the instrumental.
    """

    def _fn(self):
        from backend.api.routes.file_upload import _select_mixed_audio_files
        return _select_mixed_audio_files

    def test_excludes_existing_instrumental(self):
        fn = self._fn()
        # Listing is sorted alphabetically by GCS, so the instrumental comes first.
        files = [
            "uploads/job123/audio/existing_instrumental.mp3",
            "uploads/job123/audio/mixed.mp3",
        ]
        result = fn(files)
        assert result == ["uploads/job123/audio/mixed.mp3"]
        assert result[0] == "uploads/job123/audio/mixed.mp3"

    def test_plain_audio_only_unchanged(self):
        fn = self._fn()
        files = ["uploads/job123/audio/My Song.flac"]
        assert fn(files) == files

    def test_real_filenames_with_instrumental(self):
        fn = self._fn()
        files = [
            "uploads/job123/audio/existing_instrumental.mp3",
            "uploads/job123/audio/Eddy Grant - I Don't Wanna Dance Guide.mp3",
        ]
        assert fn(files) == ["uploads/job123/audio/Eddy Grant - I Don't Wanna Dance Guide.mp3"]


class TestApplyTenantOverrides:
    """Tests for _apply_tenant_overrides() in file_upload.py."""

    def _get_apply_fn(self):
        """Import the function under test."""
        from backend.api.routes.file_upload import _apply_tenant_overrides
        return _apply_tenant_overrides

    def test_tenant_defaults_applied_to_distribution(self):
        """Tenant brand_prefix, dropbox_path, gdrive_folder_id are applied to distribution settings."""
        apply_fn = self._get_apply_fn()
        tenant_config = _make_tenant_config()

        dist = EffectiveDistributionSettings(
            dropbox_path=None,
            gdrive_folder_id=None,
            discord_webhook_url=None,
            brand_prefix=None,
            enable_youtube_upload=True,
            youtube_description=None,
        )

        new_dist, theme, is_private, yt_upload = apply_fn(
            dist, tenant_config, "default", False, True
        )

        assert new_dist.brand_prefix == "VSTAR"
        assert new_dist.dropbox_path == "/Karaoke/Vocal-Star"
        assert new_dist.gdrive_folder_id == "gdrive-folder-123"

    def test_tenant_job_forced_private(self):
        """All tenant jobs are forced to is_private=True."""
        apply_fn = self._get_apply_fn()
        tenant_config = _make_tenant_config()

        dist = EffectiveDistributionSettings(
            dropbox_path=None, gdrive_folder_id=None, discord_webhook_url=None,
            brand_prefix=None, enable_youtube_upload=True, youtube_description=None,
        )

        _, _, is_private, _ = apply_fn(dist, tenant_config, "default", False, True)
        assert is_private is True

    def test_tenant_locked_theme_applied(self):
        """locked_theme overrides the user's theme selection."""
        apply_fn = self._get_apply_fn()
        tenant_config = _make_tenant_config()

        dist = EffectiveDistributionSettings(
            dropbox_path=None, gdrive_folder_id=None, discord_webhook_url=None,
            brand_prefix=None, enable_youtube_upload=True, youtube_description=None,
        )

        _, theme, _, _ = apply_fn(dist, tenant_config, "user-selected-theme", False, True)
        assert theme == "vocalstar"

    def test_tenant_youtube_upload_disabled(self):
        """YouTube upload is disabled for tenant jobs."""
        apply_fn = self._get_apply_fn()
        tenant_config = _make_tenant_config()

        dist = EffectiveDistributionSettings(
            dropbox_path=None, gdrive_folder_id=None, discord_webhook_url=None,
            brand_prefix=None, enable_youtube_upload=True, youtube_description=None,
        )

        _, _, _, yt_upload = apply_fn(dist, tenant_config, "default", False, True)
        assert yt_upload is False

    def test_tenant_config_overrides_global_defaults(self):
        """Tenant config is authoritative: it overrides any global/consumer default
        distribution settings already seeded into `dist`.

        Regression guard: prod sets DEFAULT_DROPBOX_PATH / DEFAULT_GDRIVE_FOLDER_ID /
        DEFAULT_BRAND_PREFIX globally, which get_effective_distribution_settings seeds
        into `dist`. If tenant overrides only filled in *unset* fields, every tenant job
        would leak into the shared consumer Dropbox folder and global Google Drive.
        """
        apply_fn = self._get_apply_fn()
        tenant_config = _make_tenant_config()

        # `dist` arrives pre-seeded with the GLOBAL consumer defaults.
        dist = EffectiveDistributionSettings(
            dropbox_path="/MediaUnsynced/Karaoke/Tracks-Organized",  # global default
            gdrive_folder_id="global-consumer-folder",               # global default
            discord_webhook_url=None,
            brand_prefix="NOMAD",                                    # global default
            enable_youtube_upload=True,
            youtube_description=None,
        )

        new_dist, _, _, _ = apply_fn(dist, tenant_config, "default", False, True)
        # Tenant config wins on every distribution field.
        assert new_dist.brand_prefix == "VSTAR"
        assert new_dist.dropbox_path == "/Karaoke/Vocal-Star"
        assert new_dist.gdrive_folder_id == "gdrive-folder-123"

    def test_gdrive_disabled_clears_global_default(self):
        """When the tenant disables gdrive_upload, the global default GDrive folder is
        cleared so the tenant job never publishes to Google Drive (the B2B requirement)."""
        apply_fn = self._get_apply_fn()
        # Tenant has a gdrive folder configured but the feature is OFF — the disabled
        # flag must win, clearing both the configured folder and any global default.
        tenant_config = _make_tenant_config()
        tenant_config.features.gdrive_upload = False

        dist = EffectiveDistributionSettings(
            dropbox_path=None,
            gdrive_folder_id="global-consumer-folder",  # would otherwise leak
            discord_webhook_url=None,
            brand_prefix=None,
            enable_youtube_upload=True,
            youtube_description=None,
        )

        new_dist, _, _, yt_upload = apply_fn(dist, tenant_config, "default", False, True)
        assert new_dist.gdrive_folder_id is None
        assert yt_upload is False

    def test_dropbox_disabled_clears_path(self):
        """When the tenant disables dropbox_upload, the Dropbox path is cleared."""
        apply_fn = self._get_apply_fn()
        tenant_config = _make_tenant_config()
        tenant_config.features.dropbox_upload = False

        dist = EffectiveDistributionSettings(
            dropbox_path="/MediaUnsynced/Karaoke/Tracks-Organized",
            gdrive_folder_id=None, discord_webhook_url=None,
            brand_prefix=None, enable_youtube_upload=True, youtube_description=None,
        )

        new_dist, _, _, _ = apply_fn(dist, tenant_config, "default", False, True)
        assert new_dist.dropbox_path is None

    def test_no_tenant_config_passthrough(self):
        """When no tenant config, all values pass through unchanged."""
        apply_fn = self._get_apply_fn()

        dist = EffectiveDistributionSettings(
            dropbox_path="/Some/Path", gdrive_folder_id=None, discord_webhook_url=None,
            brand_prefix="NOMAD", enable_youtube_upload=True, youtube_description=None,
        )

        new_dist, theme, is_private, yt_upload = apply_fn(
            dist, None, "nomad", False, True
        )
        assert new_dist.brand_prefix == "NOMAD"
        assert new_dist.dropbox_path == "/Some/Path"
        assert theme == "nomad"
        assert is_private is False
        assert yt_upload is True

    def test_partial_tenant_defaults_only_overrides_set_fields(self):
        """When tenant config has only some defaults, only those are applied."""
        apply_fn = self._get_apply_fn()
        # Only brand_prefix set, no dropbox_path or gdrive_folder_id
        tenant_config = TenantConfig(
            id="partial",
            name="Partial Tenant",
            subdomain="partial.nomadkaraoke.com",
            defaults=TenantDefaults(
                brand_prefix="PART",
                dropbox_path=None,
                gdrive_folder_id=None,
                locked_theme=None,
            ),
        )

        dist = EffectiveDistributionSettings(
            dropbox_path=None, gdrive_folder_id=None, discord_webhook_url=None,
            brand_prefix=None, enable_youtube_upload=True, youtube_description=None,
        )

        new_dist, theme, is_private, yt_upload = apply_fn(
            dist, tenant_config, "user-theme", False, True
        )

        # brand_prefix applied
        assert new_dist.brand_prefix == "PART"
        # dropbox/gdrive remain None since tenant has None
        assert new_dist.dropbox_path is None
        assert new_dist.gdrive_folder_id is None
        # No locked_theme, so user theme preserved
        assert theme == "user-theme"
        # Still forced private and no YT
        assert is_private is True
        assert yt_upload is False


class TestTenantJobDistribution:
    """Tests for get_effective_distribution_for_job() with tenant jobs."""

    def test_private_tenant_job_uses_tenant_paths(self):
        """Private tenant jobs use their own Dropbox/GDrive paths, not NonPublished."""
        job = _make_job_mock(
            is_private=True,
            tenant_id="vocalstar",
            dropbox_path="/Karaoke/Vocal-Star",
            gdrive_folder_id="gdrive-folder-123",
            brand_prefix="VSTAR",
        )

        result = get_effective_distribution_for_job(job)

        assert result.dropbox_path == "/Karaoke/Vocal-Star"
        assert result.gdrive_folder_id == "gdrive-folder-123"
        assert result.brand_prefix == "VSTAR"
        assert result.enable_youtube_upload is False

    @patch("backend.services.job_defaults_service.get_settings")
    def test_non_tenant_private_job_still_uses_nonpublished(self, mock_settings):
        """Non-tenant private jobs still get the NonPublished path override."""
        mock_settings.return_value = MagicMock(
            default_private_dropbox_path="/Tracks-NonPublished",
            default_private_brand_prefix="NOMADNP",
        )

        job = _make_job_mock(
            is_private=True,
            tenant_id=None,
            dropbox_path="/Some/User/Path",
            brand_prefix="SOMETHING",
        )

        result = get_effective_distribution_for_job(job)

        assert result.dropbox_path == "/Tracks-NonPublished"
        assert result.brand_prefix == "NOMADNP"
        assert result.enable_youtube_upload is False
        assert result.gdrive_folder_id is None

    def test_non_private_job_uses_own_settings(self):
        """Non-private jobs (tenant or not) use their own distribution settings."""
        job = _make_job_mock(
            is_private=False,
            tenant_id="vocalstar",
            dropbox_path="/Karaoke/Vocal-Star",
            gdrive_folder_id="gdrive-folder-123",
            brand_prefix="VSTAR",
            enable_youtube_upload=False,
        )

        result = get_effective_distribution_for_job(job)

        assert result.dropbox_path == "/Karaoke/Vocal-Star"
        assert result.gdrive_folder_id == "gdrive-folder-123"
        assert result.brand_prefix == "VSTAR"
