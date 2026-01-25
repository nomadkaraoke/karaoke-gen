"""
API endpoint tests for instrumental review functionality.

Note: Most standalone instrumental endpoints were removed in the combined review flow.
This file now only tests request models that are still used.
"""

import pytest
from pydantic import ValidationError


class TestRequestModels:
    """Tests for API request models used in instrumental selection."""

    def test_mute_region_request_valid(self):
        """MuteRegionRequest should accept valid values."""
        from backend.models.requests import MuteRegionRequest

        region = MuteRegionRequest(start_seconds=10.0, end_seconds=20.0)

        assert region.start_seconds == 10.0
        assert region.end_seconds == 20.0

    def test_mute_region_request_invalid_start(self):
        """MuteRegionRequest should reject negative start."""
        from backend.models.requests import MuteRegionRequest

        with pytest.raises(ValidationError):
            MuteRegionRequest(start_seconds=-1.0, end_seconds=20.0)

    def test_mute_region_request_invalid_order(self):
        """MuteRegionRequest should reject end before start."""
        from backend.models.requests import MuteRegionRequest

        with pytest.raises(ValidationError):
            MuteRegionRequest(start_seconds=20.0, end_seconds=10.0)

    def test_create_custom_instrumental_request_valid(self):
        """CreateCustomInstrumentalRequest should accept valid mute regions."""
        from backend.models.requests import (
            CreateCustomInstrumentalRequest,
            MuteRegionRequest,
        )

        request = CreateCustomInstrumentalRequest(
            mute_regions=[
                MuteRegionRequest(start_seconds=10.0, end_seconds=20.0),
                MuteRegionRequest(start_seconds=60.0, end_seconds=80.0),
            ]
        )

        assert len(request.mute_regions) == 2

    def test_create_custom_instrumental_request_empty_regions(self):
        """CreateCustomInstrumentalRequest should reject empty mute regions."""
        from backend.models.requests import CreateCustomInstrumentalRequest

        with pytest.raises(ValidationError) as exc_info:
            CreateCustomInstrumentalRequest(mute_regions=[])

        assert "At least one mute region is required" in str(exc_info.value)


class TestInstrumentalSelectionModel:
    """Tests for InstrumentalSelection request model."""

    def test_instrumental_selection_accepts_custom(self):
        """InstrumentalSelection should accept 'custom' as valid selection."""
        from backend.models.requests import InstrumentalSelection

        selection = InstrumentalSelection(selection="custom")
        assert selection.selection == "custom"

    def test_instrumental_selection_accepts_clean(self):
        """InstrumentalSelection should accept 'clean' as valid selection."""
        from backend.models.requests import InstrumentalSelection

        selection = InstrumentalSelection(selection="clean")
        assert selection.selection == "clean"

    def test_instrumental_selection_accepts_with_backing(self):
        """InstrumentalSelection should accept 'with_backing' as valid selection."""
        from backend.models.requests import InstrumentalSelection

        selection = InstrumentalSelection(selection="with_backing")
        assert selection.selection == "with_backing"

    def test_instrumental_selection_rejects_invalid(self):
        """InstrumentalSelection should reject invalid values."""
        from backend.models.requests import InstrumentalSelection

        with pytest.raises(ValidationError):
            InstrumentalSelection(selection="invalid_option")
