"""
Tests for gdrive_validator/main.py

Key regression test: NOMAD-1276
The validator was reporting "no gaps" even when a folder (e.g. CDG) was missing
the latest track(s) that existed in other folders (MP4, MP4-720p).

Root cause: gap detection used each folder's own max sequence number as the
upper bound, so a missing track at the tail of a folder was invisible — the
range checked never included it.

Fix: use the global max across all folders as the upper bound for every folder's
gap check.
"""
import sys
import os
from unittest.mock import MagicMock

# Stub out Cloud Function dependencies that aren't installed in the test env
for mod in ("functions_framework", "google.oauth2.service_account",
            "googleapiclient.discovery", "googleapiclient.errors"):
    sys.modules.setdefault(mod, MagicMock())

# Add the function directory to the path so we can import main directly
sys.path.insert(0, os.path.dirname(__file__))

from main import (  # noqa: E402
    validate_files,
    validate_filename_format,
    get_sequence_number,
    expand_gaps,
    has_issues,
    check_track_completeness,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_mp4(n: int) -> str:
    return f"NOMAD-{n:04d} - Artist - Title.mp4"


def make_cdg(n: int) -> str:
    return f"NOMAD-{n:04d} - Artist - Title.zip"


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestExpandGaps:
    def test_single_numbers(self):
        assert expand_gaps([1, 3, 5]) == {1, 3, 5}

    def test_range(self):
        assert expand_gaps([(2, 4)]) == {2, 3, 4}

    def test_mixed(self):
        assert expand_gaps([1, (3, 5), 7]) == {1, 3, 4, 5, 7}

    def test_empty(self):
        assert expand_gaps([]) == set()


class TestValidateFilenameFormat:
    def test_valid_mp4(self):
        assert validate_filename_format("NOMAD-1234 - Artist - Title.mp4")

    def test_valid_zip(self):
        assert validate_filename_format("NOMAD-0001 - Artist - Title.zip")

    def test_invalid_missing_seq(self):
        assert not validate_filename_format("Artist - Title.mp4")

    def test_invalid_short_seq(self):
        assert not validate_filename_format("NOMAD-12 - Artist - Title.mp4")

    def test_ignored_file_passes(self):
        assert validate_filename_format(".DS_Store")

    def test_ignored_file_passes_keep(self):
        assert validate_filename_format(".gitkeep")


class TestCheckTrackCompleteness:
    """Incident-hardening D2: same-run per-brand-code completeness."""

    def _folders(self, present):
        """Build files_by_folder where `present` is a set of folder names that
        contain NOMAD-1632 (all folders also carry an unrelated track)."""
        out = {}
        for folder, ext in (("MP4", "mp4"), ("MP4-720p", "mp4"), ("CDG", "zip")):
            files = [f"NOMAD-1600 - Other - Song.{ext}"]
            if folder in present:
                files.append(f"NOMAD-1632 - Artist - Title.{ext}")
            out[folder] = files
        return out

    def test_present_everywhere_no_missing(self):
        files = self._folders({"MP4", "MP4-720p", "CDG"})
        assert check_track_completeness(files, "NOMAD-1632", expect_cdg=True) == []

    def test_missing_from_720p_is_the_nomad1632_case(self):
        files = self._folders({"MP4", "CDG"})  # 720p missing — the real incident
        assert check_track_completeness(files, "NOMAD-1632", expect_cdg=True) == ["MP4-720p"]

    def test_missing_from_multiple(self):
        files = self._folders({"MP4"})
        assert check_track_completeness(files, "NOMAD-1632", expect_cdg=True) == ["MP4-720p", "CDG"]

    def test_cdg_not_required_by_default(self):
        """Video-only release (no CDG produced) must NOT report CDG missing."""
        files = self._folders({"MP4", "MP4-720p"})  # no CDG
        assert check_track_completeness(files, "NOMAD-1632") == []
        assert check_track_completeness(files, "NOMAD-1632", expect_cdg=False) == []

    def test_missing_cdg_only_when_expected(self):
        files = self._folders({"MP4", "MP4-720p"})  # no CDG
        assert check_track_completeness(files, "NOMAD-1632", expect_cdg=True) == ["CDG"]

    def test_accepts_bare_number(self):
        files = self._folders({"MP4", "MP4-720p", "CDG"})
        assert check_track_completeness(files, "1632", expect_cdg=True) == []

    def test_accepts_lowercase(self):
        files = self._folders({"MP4", "CDG"})
        assert check_track_completeness(files, "nomad-1632", expect_cdg=True) == ["MP4-720p"]

    def test_prefix_not_substring(self):
        # NOMAD-163 must NOT satisfy a NOMAD-1632 check (prefix includes trailing space)
        files = {
            "MP4": ["NOMAD-1632 - A - B.mp4"],
            "MP4-720p": ["NOMAD-1632 - A - B.mp4"],
            "CDG": ["NOMAD-1632 - A - B.zip"],
        }
        assert check_track_completeness(files, "NOMAD-163", expect_cdg=True) == ["MP4", "MP4-720p", "CDG"]

    def test_has_issues_flags_missing_for_track(self):
        assert has_issues({
            'duplicates': {}, 'invalid_filenames': {}, 'gaps': {},
            'missing_for_track': {'NOMAD-1632': ['MP4-720p']},
        })


class TestGetSequenceNumber:
    def test_normal(self):
        assert get_sequence_number("NOMAD-1234 - Artist - Title.mp4") == 1234

    def test_leading_zeros(self):
        assert get_sequence_number("NOMAD-0001 - A - B.zip") == 1

    def test_no_match(self):
        assert get_sequence_number("random_file.mp4") is None


# ---------------------------------------------------------------------------
# Core gap detection tests
# ---------------------------------------------------------------------------

class TestValidateFilesNoIssues:
    def test_clean_folders(self):
        files = {
            "MP4": [make_mp4(i) for i in range(1, 6)],
            "MP4-720p": [make_mp4(i) for i in range(1, 6)],
            "CDG": [make_cdg(i) for i in range(1, 6)],
        }
        issues = validate_files(files)
        assert not has_issues(issues)

    def test_known_gap_not_flagged(self):
        # NOMAD-532 is a known gap for MP4/MP4-720p
        nums = list(range(1, 600))
        nums.remove(532)
        files = {
            "MP4": [make_mp4(i) for i in nums],
            "MP4-720p": [make_mp4(i) for i in nums],
        }
        issues = validate_files(files)
        assert not has_issues(issues)


class TestValidateFilesInternalGap:
    def test_detects_gap_within_folder(self):
        # MP4 is missing NOMAD-0003 — an interior gap not in KNOWN_GAPS
        nums = [1, 2, 4, 5]
        files = {"MP4": [make_mp4(i) for i in nums]}
        issues = validate_files(files)
        assert has_issues(issues)
        assert 3 in issues["gaps"]["MP4"]


class TestValidateFilesTrailingGap:
    """
    Regression tests for NOMAD-1276.

    The bug: if CDG is missing the latest track (e.g. NOMAD-1277) but MP4 has it,
    the validator used to say "no gaps" because it only checked up to CDG's own max.
    """

    def test_missing_latest_cdg_is_detected(self):
        """CDG missing the highest-numbered track that exists in MP4.

        Use numbers 100-104: well clear of all CDG KNOWN_GAPS entries.
        """
        mp4_nums = list(range(100, 105))   # 100-104
        cdg_nums = list(range(100, 104))   # 100-103 (NOMAD-0104 CDG is missing)

        files = {
            "MP4": [make_mp4(i) for i in mp4_nums],
            "MP4-720p": [make_mp4(i) for i in mp4_nums],
            "CDG": [make_cdg(i) for i in cdg_nums],
        }
        issues = validate_files(files)
        assert has_issues(issues), "Expected gap to be detected in CDG"
        assert 104 in issues["gaps"]["CDG"], "NOMAD-104 should be flagged as missing from CDG"

    def test_missing_latest_mp4_is_detected(self):
        """MP4 missing the highest-numbered track that exists in CDG."""
        mp4_nums = list(range(100, 104))   # 100-103
        cdg_nums = list(range(100, 105))   # 100-104

        files = {
            "MP4": [make_mp4(i) for i in mp4_nums],
            "CDG": [make_cdg(i) for i in cdg_nums],
        }
        issues = validate_files(files)
        assert has_issues(issues)
        assert 104 in issues["gaps"]["MP4"]

    def test_multiple_missing_trailing_cdg(self):
        """CDG missing the last two tracks (numbers clear of KNOWN_GAPS)."""
        mp4_nums = list(range(100, 107))   # 100-106
        cdg_nums = list(range(100, 105))   # 100-104  (105 and 106 CDG missing)

        files = {
            "MP4": [make_mp4(i) for i in mp4_nums],
            "CDG": [make_cdg(i) for i in cdg_nums],
        }
        issues = validate_files(files)
        assert has_issues(issues)
        cdg_gaps = issues["gaps"]["CDG"]
        assert 105 in cdg_gaps
        assert 106 in cdg_gaps

    def test_no_false_positive_when_cdg_legitimately_shorter(self):
        """CDG tracks that are intentionally absent are in KNOWN_GAPS — no false alarm."""
        mp4_nums = list(range(100, 105))
        cdg_nums = list(range(100, 105))

        files = {
            "MP4": [make_mp4(i) for i in mp4_nums],
            "CDG": [make_cdg(i) for i in cdg_nums],
        }
        issues = validate_files(files)
        assert not has_issues(issues)

    def test_real_world_scenario(self):
        """
        Approximation of the NOMAD-1276 incident:
        - MP4 and MP4-720p have NOMAD-1277 (1276 files after excluding 532)
        - CDG only goes up to NOMAD-1276 (1277 is missing, not in known gaps)
        """
        # Build MP4: 1-1277, skipping 532
        mp4_nums = [i for i in range(1, 1278) if i != 532]
        # CDG: 1-1276, minus the large known gaps (simplified subset)
        # Just use 1-1276 minus 532 to keep the test fast and focused
        cdg_nums = [i for i in range(1, 1277) if i != 532]

        files = {
            "MP4": [make_mp4(i) for i in mp4_nums],
            "MP4-720p": [make_mp4(i) for i in mp4_nums],
            "CDG": [make_cdg(i) for i in cdg_nums],
        }
        issues = validate_files(files)
        # CDG should show NOMAD-1277 as a gap (it's the global max but missing from CDG)
        assert has_issues(issues), "CDG missing NOMAD-1277 should be detected"
        assert "CDG" in issues["gaps"]
        assert 1277 in issues["gaps"]["CDG"]
        # MP4 and MP4-720p should be clean
        assert "MP4" not in issues["gaps"]
        assert "MP4-720p" not in issues["gaps"]


class TestValidateFilesDuplicates:
    def test_detects_duplicate_sequence(self):
        files = {
            "MP4": [
                "NOMAD-0001 - Artist A - Song A.mp4",
                "NOMAD-0001 - Artist B - Song B.mp4",
                "NOMAD-0002 - Artist C - Song C.mp4",
            ]
        }
        issues = validate_files(files)
        assert has_issues(issues)
        assert 1 in issues["duplicates"]["MP4"]


class TestValidateFilesInvalidFilenames:
    def test_detects_invalid_filename(self):
        files = {
            "MP4": [
                "NOMAD-0001 - Artist - Title.mp4",
                "not_a_karaoke_file.mp4",
            ]
        }
        issues = validate_files(files)
        assert has_issues(issues)
        assert "not_a_karaoke_file.mp4" in issues["invalid_filenames"]["MP4"]

    def test_ignored_files_not_flagged(self):
        files = {
            "MP4": [
                "NOMAD-0001 - Artist - Title.mp4",
                ".DS_Store",
                "kj-nomad.index.json",
            ]
        }
        issues = validate_files(files)
        assert not has_issues(issues)


class TestHasIssues:
    def test_no_issues(self):
        assert not has_issues({"duplicates": {}, "invalid_filenames": {}, "gaps": {}})

    def test_has_duplicates(self):
        assert has_issues({"duplicates": {"MP4": {1: ["a", "b"]}}, "invalid_filenames": {}, "gaps": {}})

    def test_has_invalid(self):
        assert has_issues({"duplicates": {}, "invalid_filenames": {"MP4": ["x"]}, "gaps": {}})

    def test_has_gaps(self):
        assert has_issues({"duplicates": {}, "invalid_filenames": {}, "gaps": {"MP4": [3]}})
