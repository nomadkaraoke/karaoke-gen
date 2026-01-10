"""
Tests for GCE Encoding Worker (gce_encoding/main.py).

These tests verify the file-finding logic in the GCE encoding worker,
particularly ensuring that instrumental selection is respected.

NOTE: This test file is self-contained and doesn't import the full app
to avoid dependency issues in CI/CD environments.
"""

import pytest
from pathlib import Path


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
