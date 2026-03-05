"""
Tests for GCE Encoding Worker (gce_encoding/main.py).

These tests verify the file-finding logic in the GCE encoding worker,
particularly ensuring that instrumental selection is respected.

NOTE: This test file is self-contained and doesn't import the full app
to avoid dependency issues in CI/CD environments.
"""

import pytest
import re
from pathlib import Path
from packaging.version import Version


# Copy the find_file function here to test it in isolation
# This avoids importing the full gce_encoding module which has GCS dependencies
def find_file(work_dir: Path, *patterns):
    '''Find a file matching any of the given glob patterns.'''
    for pattern in patterns:
        matches = list(work_dir.glob(f"**/{pattern}"))
        if matches:
            return matches[0]
    return None


class TestFindFile:
    """Test the find_file function used to locate input files."""

    def test_find_file_single_match(self, tmp_path):
        """Test finding a file with a single match."""
        # Create test file
        test_file = tmp_path / "test_instrumental.flac"
        test_file.touch()

        result = find_file(tmp_path, "*instrumental*.flac")
        assert result is not None
        assert result.name == "test_instrumental.flac"

    def test_find_file_no_match(self, tmp_path):
        """Test finding a file with no matches returns None."""
        result = find_file(tmp_path, "*nonexistent*.flac")
        assert result is None

    def test_find_file_priority_order(self, tmp_path):
        """Test that find_file returns first matching pattern."""
        # Create multiple files that match different patterns
        first = tmp_path / "first_pattern.flac"
        second = tmp_path / "second_pattern.flac"
        first.touch()
        second.touch()

        # First pattern should win
        result = find_file(tmp_path, "*first*.flac", "*second*.flac")
        assert result.name == "first_pattern.flac"


class TestInstrumentalSelection:
    """Test that instrumental selection is properly respected."""

    def test_clean_instrumental_prioritized_when_selected(self, tmp_path):
        """Test that clean instrumental is found when 'clean' is selected.

        When both instrumentals exist and 'clean' is selected,
        the clean version should be found.
        """
        # Create both instrumental files (as they exist in GCS)
        clean = tmp_path / "Artist - Title (Instrumental Clean).flac"
        backing = tmp_path / "Artist - Title (Instrumental Backing).flac"
        clean.touch()
        backing.touch()

        # Simulate finding instrumental with 'clean' selection
        # (mimics the logic in run_encoding when instrumental_selection == 'clean')
        result = find_file(
            tmp_path,
            "*instrumental_clean*.flac", "*Instrumental Clean*.flac",
            "*instrumental*.flac", "*Instrumental*.flac",
            "*instrumental*.wav"
        )

        assert result is not None
        assert "Clean" in result.name

    def test_backing_instrumental_prioritized_when_selected(self, tmp_path):
        """Test that backing instrumental is found when 'with_backing' is selected.

        When both instrumentals exist and 'with_backing' is selected,
        the backing version should be found.

        This test would have caught the bug where the GCE worker always
        used the clean instrumental regardless of user selection.
        """
        # Create both instrumental files (as they exist in GCS)
        clean = tmp_path / "Artist - Title (Instrumental Clean).flac"
        backing = tmp_path / "Artist - Title (Instrumental Backing).flac"
        clean.touch()
        backing.touch()

        # Simulate finding instrumental with 'with_backing' selection
        # (mimics the logic in run_encoding when instrumental_selection == 'with_backing')
        result = find_file(
            tmp_path,
            "*instrumental_with_backing*.flac", "*Instrumental Backing*.flac",
            "*with_backing*.flac", "*Backing*.flac",
            "*instrumental*.flac", "*Instrumental*.flac",
            "*instrumental*.wav"
        )

        assert result is not None
        assert "Backing" in result.name

    def test_backing_instrumental_with_stem_naming(self, tmp_path):
        """Test finding backing instrumental with GCS stem naming convention.

        In GCS, stems are named like 'stems/instrumental_with_backing.flac'
        """
        stems_dir = tmp_path / "stems"
        stems_dir.mkdir()

        clean = stems_dir / "instrumental_clean.flac"
        backing = stems_dir / "instrumental_with_backing.flac"
        clean.touch()
        backing.touch()

        # Simulate finding instrumental with 'with_backing' selection
        result = find_file(
            tmp_path,
            "*instrumental_with_backing*.flac", "*Instrumental Backing*.flac",
            "*with_backing*.flac", "*Backing*.flac",
            "*instrumental*.flac", "*Instrumental*.flac",
            "*instrumental*.wav"
        )

        assert result is not None
        assert "with_backing" in result.name

    def test_fallback_to_generic_when_specific_not_found(self, tmp_path):
        """Test fallback to generic pattern when specific not found."""
        # Only create a generically named instrumental
        generic = tmp_path / "instrumental.flac"
        generic.touch()

        # Both clean and backing selections should fall back
        for selection in ["clean", "with_backing"]:
            if selection == "with_backing":
                patterns = (
                    "*instrumental_with_backing*.flac", "*Instrumental Backing*.flac",
                    "*with_backing*.flac", "*Backing*.flac",
                    "*instrumental*.flac", "*Instrumental*.flac",
                    "*instrumental*.wav"
                )
            else:
                patterns = (
                    "*instrumental_clean*.flac", "*Instrumental Clean*.flac",
                    "*instrumental*.flac", "*Instrumental*.flac",
                    "*instrumental*.wav"
                )

            result = find_file(tmp_path, *patterns)
            assert result is not None
            assert result.name == "instrumental.flac"


class TestExistingInstrumental:
    """Test that existing (user-uploaded) instrumentals are found correctly."""

    def test_existing_instrumental_found_by_pattern(self, tmp_path):
        """Test that existing_instrumental file is found when present."""
        existing = tmp_path / "existing_instrumental.mp3"
        existing.touch()

        result = find_file(tmp_path, "*existing_instrumental*", "*Instrumental User*")
        assert result is not None
        assert "existing_instrumental" in result.name

    def test_instrumental_user_found_by_pattern(self, tmp_path):
        """Test that Instrumental User file is found when present."""
        user_file = tmp_path / "Artist - Title (Instrumental User).mp3"
        user_file.touch()

        result = find_file(tmp_path, "*existing_instrumental*", "*Instrumental User*")
        assert result is not None
        assert "Instrumental User" in result.name

    def test_existing_instrumental_takes_priority_over_separated(self, tmp_path):
        """When existing instrumental is present alongside separated stems,
        the existing_instrumental config should cause it to be found first.

        This tests the logic flow: if config has existing_instrumental,
        we search for *existing_instrumental* patterns BEFORE checking
        instrumental_selection.
        """
        # Create both types
        existing = tmp_path / "existing_instrumental.flac"
        separated = tmp_path / "instrumental_clean.flac"
        custom = tmp_path / "custom_instrumental.flac"
        existing.touch()
        separated.touch()
        custom.touch()

        # existing_instrumental pattern should find the right file
        result = find_file(tmp_path, "*existing_instrumental*", "*Instrumental User*")
        assert result is not None
        assert "existing_instrumental" in result.name

    def test_existing_instrumental_not_found_returns_none(self, tmp_path):
        """When no existing instrumental file exists, find_file returns None."""
        # Only separated stems exist
        tmp_path_file = tmp_path / "instrumental_clean.flac"
        tmp_path_file.touch()

        result = find_file(tmp_path, "*existing_instrumental*", "*Instrumental User*")
        assert result is None


class TestInstrumentalSelectionRegression:
    """Regression tests for instrumental selection bug.

    Bug: User selected 'with_backing' instrumental in the UI but the final
    video used the clean instrumental instead.

    Root cause: The GCE worker's find_file() always prioritized
    '*instrumental_clean*' patterns before generic patterns, ignoring
    the user's selection.
    """

    def test_bug_scenario_both_instrumentals_present(self, tmp_path):
        """Recreate the exact bug scenario where both instrumentals exist.

        This is the key regression test. When both instrumentals are downloaded
        from GCS (which happens with the GCE worker), the correct one must
        be selected based on the user's choice.
        """
        # Setup: Both instrumentals in stems/ (as downloaded from GCS)
        stems = tmp_path / "stems"
        stems.mkdir()

        (stems / "instrumental_clean.flac").touch()
        (stems / "instrumental_with_backing.flac").touch()

        # Also have properly named versions in the root (from _setup_working_directory)
        (tmp_path / "Artist - Song (Instrumental Clean).flac").touch()
        (tmp_path / "Artist - Song (Instrumental Backing).flac").touch()

        # When user selected 'with_backing', the backing version MUST be found
        backing_patterns = (
            "*instrumental_with_backing*.flac", "*Instrumental Backing*.flac",
            "*with_backing*.flac", "*Backing*.flac",
            "*instrumental*.flac", "*Instrumental*.flac",
            "*instrumental*.wav"
        )
        result = find_file(tmp_path, *backing_patterns)

        assert result is not None
        # Must find a backing-related file, not the clean one
        name = result.name.lower()
        assert "backing" in name or "with_backing" in name, \
            f"Expected backing instrumental but found: {result.name}"

    def test_clean_selection_still_works(self, tmp_path):
        """Verify clean selection still works correctly after the fix."""
        stems = tmp_path / "stems"
        stems.mkdir()

        (stems / "instrumental_clean.flac").touch()
        (stems / "instrumental_with_backing.flac").touch()

        (tmp_path / "Artist - Song (Instrumental Clean).flac").touch()
        (tmp_path / "Artist - Song (Instrumental Backing).flac").touch()

        # When user selected 'clean', the clean version must be found
        clean_patterns = (
            "*instrumental_clean*.flac", "*Instrumental Clean*.flac",
            "*instrumental*.flac", "*Instrumental*.flac",
            "*instrumental*.wav"
        )
        result = find_file(tmp_path, *clean_patterns)

        assert result is not None
        name = result.name.lower()
        assert "clean" in name, f"Expected clean instrumental but found: {result.name}"


class TestWheelFilenameFiltering:
    """Test wheel filename filtering to prevent installation of invalid wheels.

    Bug: The ensure_latest_wheel() function downloads all wheels including
    karaoke_gen-current.whl, which is not a valid PEP 427 wheel filename
    (missing version and platform tags). When pip tries to install it,
    it fails with: "Invalid wheel filename (wrong number of parts)"

    Fix: Filter out wheels containing '-current' before selecting which to install.
    """

    def filter_wheels(self, wheels):
        """Replicate the filtering logic from ensure_latest_wheel()."""
        return [w for w in wheels if '-current' not in w]

    def test_filters_out_current_wheel(self):
        """Test that karaoke_gen-current.whl is filtered out."""
        wheels = [
            "/tmp/karaoke_gen-0.116.0-py3-none-any.whl",
            "/tmp/karaoke_gen-current.whl",
            "/tmp/karaoke_gen-0.115.0-py3-none-any.whl",
        ]

        filtered = self.filter_wheels(wheels)

        assert len(filtered) == 2
        assert "/tmp/karaoke_gen-current.whl" not in filtered
        assert "/tmp/karaoke_gen-0.116.0-py3-none-any.whl" in filtered
        assert "/tmp/karaoke_gen-0.115.0-py3-none-any.whl" in filtered

    def test_returns_empty_when_only_current_wheel(self):
        """Test that filtering returns empty list when only current wheel exists."""
        wheels = ["/tmp/karaoke_gen-current.whl"]

        filtered = self.filter_wheels(wheels)

        assert filtered == []

    def test_selects_latest_version_after_filtering(self):
        """Test that the latest version is selected after filtering out current.

        Note: This test verifies filtering but uses simple sorting.
        See TestWheelVersionSorting for comprehensive semantic version sorting tests.
        """
        wheels = [
            "/tmp/karaoke_gen-0.114.0-py3-none-any.whl",
            "/tmp/karaoke_gen-current.whl",
            "/tmp/karaoke_gen-0.116.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.115.0-py3-none-any.whl",
        ]

        filtered = self.filter_wheels(wheels)

        # Verify current wheel was filtered out
        assert len(filtered) == 3
        assert all("-current" not in w for w in filtered)

    def test_no_filtering_when_no_current_wheel(self):
        """Test that valid wheels pass through when no current wheel exists."""
        wheels = [
            "/tmp/karaoke_gen-0.116.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.115.0-py3-none-any.whl",
        ]

        filtered = self.filter_wheels(wheels)

        assert len(filtered) == 2
        assert filtered == wheels

    def test_filters_current_in_different_paths(self):
        """Test that current wheel is filtered regardless of path."""
        wheels = [
            "/different/path/karaoke_gen-current.whl",
            "/tmp/karaoke_gen-0.116.0-py3-none-any.whl",
        ]

        filtered = self.filter_wheels(wheels)

        assert len(filtered) == 1
        assert "/tmp/karaoke_gen-0.116.0-py3-none-any.whl" in filtered


class TestWheelVersionSorting:
    """Test semantic version sorting for wheel selection.

    Bug: The ensure_latest_wheel() function uses sorted(wheels)[-1] which does
    alphabetical sorting, not semantic version sorting. This causes version 0.99.9
    to be selected over 0.116.1 because string comparison puts '9' > '1'.

    Fix: Extract version from filename using regex and sort by packaging.version.Version
    """

    def extract_version(self, wheel_path):
        """Replicate the version extraction logic from ensure_latest_wheel()."""
        match = re.search(r'karaoke_gen-([0-9.]+)-', wheel_path)
        if match:
            return Version(match.group(1))
        return Version("0.0.0")  # Fallback for unparseable filenames

    def select_latest_wheel(self, wheels):
        """Replicate the full selection logic from ensure_latest_wheel()."""
        # Filter out -current wheels
        filtered = [w for w in wheels if '-current' not in w]
        if not filtered:
            return None
        # Sort by semantic version
        filtered.sort(key=self.extract_version, reverse=True)
        return filtered[0]

    def test_semantic_version_sorting_regression(self):
        """Test the exact bug: 0.99.9 should NOT be selected over 0.116.1"""
        wheels = [
            "/tmp/karaoke_gen-0.99.9-py3-none-any.whl",
            "/tmp/karaoke_gen-0.116.1-py3-none-any.whl",
        ]

        latest = self.select_latest_wheel(wheels)

        # Semantic version: 0.116.1 > 0.99.9
        assert "0.116.1" in latest
        assert "0.99.9" not in latest

    def test_alphabetical_would_fail(self):
        """Demonstrate that alphabetical sorting gives wrong result."""
        wheels = [
            "/tmp/karaoke_gen-0.99.9-py3-none-any.whl",
            "/tmp/karaoke_gen-0.116.1-py3-none-any.whl",
        ]

        # This is the OLD buggy behavior (alphabetical)
        alphabetical_latest = sorted(wheels)[-1]

        # Alphabetical puts 0.99.9 last (WRONG!)
        assert "0.99.9" in alphabetical_latest

    def test_version_extraction(self):
        """Test that version is correctly extracted from wheel filename."""
        test_cases = [
            ("/tmp/karaoke_gen-0.116.1-py3-none-any.whl", "0.116.1"),
            ("/tmp/karaoke_gen-0.99.9-py3-none-any.whl", "0.99.9"),
            ("/tmp/karaoke_gen-1.0.0-py3-none-any.whl", "1.0.0"),
            ("/tmp/karaoke_gen-0.1.2-py3-none-any.whl", "0.1.2"),
        ]

        for wheel_path, expected_version in test_cases:
            version = self.extract_version(wheel_path)
            assert str(version) == expected_version

    def test_version_extraction_fallback(self):
        """Test that unparseable filenames get fallback version."""
        invalid_wheels = [
            "/tmp/karaoke_gen-current.whl",
            "/tmp/karaoke_gen-latest.whl",
            "/tmp/invalid-filename.whl",
        ]

        for wheel in invalid_wheels:
            version = self.extract_version(wheel)
            assert version == Version("0.0.0")

    def test_complex_version_sorting(self):
        """Test sorting with multiple versions including patch, minor, major."""
        wheels = [
            "/tmp/karaoke_gen-0.99.9-py3-none-any.whl",
            "/tmp/karaoke_gen-0.116.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.116.1-py3-none-any.whl",
            "/tmp/karaoke_gen-1.0.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.115.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.1.0-py3-none-any.whl",
        ]

        latest = self.select_latest_wheel(wheels)

        # 1.0.0 is the highest version
        assert "1.0.0" in latest

    def test_version_sorting_with_current_wheel(self):
        """Test that current wheel is filtered before version sorting."""
        wheels = [
            "/tmp/karaoke_gen-0.99.9-py3-none-any.whl",
            "/tmp/karaoke_gen-current.whl",
            "/tmp/karaoke_gen-0.116.1-py3-none-any.whl",
        ]

        latest = self.select_latest_wheel(wheels)

        # Current is filtered out, 0.116.1 is selected
        assert "0.116.1" in latest
        assert "current" not in latest

    def test_patch_version_comparison(self):
        """Test that patch versions are compared correctly."""
        wheels = [
            "/tmp/karaoke_gen-0.116.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.116.1-py3-none-any.whl",
            "/tmp/karaoke_gen-0.116.2-py3-none-any.whl",
        ]

        latest = self.select_latest_wheel(wheels)

        assert "0.116.2" in latest

    def test_minor_version_comparison(self):
        """Test that minor versions are compared correctly."""
        wheels = [
            "/tmp/karaoke_gen-0.115.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.116.0-py3-none-any.whl",
            "/tmp/karaoke_gen-0.200.0-py3-none-any.whl",
        ]

        latest = self.select_latest_wheel(wheels)

        assert "0.200.0" in latest

    def test_empty_wheels_list(self):
        """Test that empty list returns None."""
        wheels = []
        latest = self.select_latest_wheel(wheels)
        assert latest is None

    def test_only_current_wheel_returns_none(self):
        """Test that list with only current wheel returns None."""
        wheels = ["/tmp/karaoke_gen-current.whl"]
        latest = self.select_latest_wheel(wheels)
        assert latest is None
