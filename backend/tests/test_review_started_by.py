"""JobManager.record_review_started — who opened the lyrics review first."""
from unittest.mock import MagicMock

import pytest

from backend.services.job_manager import JobManager


@pytest.fixture
def jm():
    m = JobManager.__new__(JobManager)
    m.update_job = MagicMock()
    return m


@pytest.mark.parametrize("reviewer,is_admin,expected", [
    ("singer@example.com", False, "owner"),
    ("SINGER@example.com", False, "owner"),
    (None, False, "owner"),                      # review-token link (sent to the owner)
    ("andrew@nomadkaraoke.com", True, "admin"),  # the KJ / support
    ("singer@example.com", True, "owner"),       # an admin reviewing their own job
    (None, True, "admin"),                       # admin token without an email
])
def test_started_by(jm, reviewer, is_admin, expected):
    assert jm.record_review_started("j1", "singer@example.com", reviewer, is_admin) == expected
    fields = jm.update_job.call_args.args[1]
    assert fields["state_data.review_started_by"] == expected
    assert "state_data.review_started_at" in fields


def test_write_failure_is_not_fatal(jm):
    jm.update_job.side_effect = RuntimeError("firestore down")
    assert jm.record_review_started("j1", "a@b.com", None, False) == "owner"
