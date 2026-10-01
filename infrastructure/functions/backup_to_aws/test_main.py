"""Tests for main.backup_to_aws cadence gating (weekly git step, S3 excludes)."""

import datetime
from unittest.mock import MagicMock, patch

import main


class _FakeDate(datetime.date):
    today_value = datetime.date(2026, 9, 28)  # a Monday

    @classmethod
    def today(cls):
        return cls.today_value


def _run(today):
    _FakeDate.today_value = today
    request = MagicMock()
    request.args = {}
    with patch("main.datetime.date", _FakeDate), \
         patch("main.export_firestore", return_value="fs"), \
         patch("main.export_bigquery_tables", return_value={}), \
         patch("main.sync_gcs_to_staging", return_value="gcs"), \
         patch("main.export_secrets", return_value="sec"), \
         patch("main._run_git_repos_backup", return_value="git") as git, \
         patch("main.upload_staging_to_s3", return_value="s3") as upload, \
         patch("main.send_alert"):
        main.backup_to_aws(request)
    return git, upload


def test_weekday_skips_git_and_holds_back_firestore_and_git_repos():
    git, upload = _run(datetime.date(2026, 9, 29))  # Tuesday
    git.assert_not_called()
    assert set(upload.call_args.kwargs["exclude_prefixes"]) == {"firestore/", "git-repos/"}


def test_sunday_runs_incremental_git_and_uploads_everything():
    git, upload = _run(datetime.date(2026, 9, 27))  # Sunday, day 27
    git.assert_called_once_with(full_refresh=False)
    assert upload.call_args.kwargs["exclude_prefixes"] == []


def test_first_sunday_of_month_is_full_refresh():
    git, _ = _run(datetime.date(2026, 10, 4))  # first Sunday of October
    git.assert_called_once_with(full_refresh=True)


def test_manual_git_mode_only_uploads_git_prefix():
    request = MagicMock()
    request.args = {"mode": "git_repos", "full": "1"}
    with patch("main._run_git_repos_backup", return_value="git") as git, \
         patch("main.upload_staging_to_s3", return_value="s3") as upload, \
         patch("main.export_firestore") as fs:
        body, status = main.backup_to_aws(request)
    assert status == 200
    git.assert_called_once_with(full_refresh=True)
    assert upload.call_args.kwargs["include_prefixes"] == ["git-repos/"]
    fs.assert_not_called()


def test_job_files_bucket_is_not_synced_off_site():
    """Job files stopped going to S3 on 2026-10-01 (egress cost cut); only the
    small kn-data bucket is still delta-synced to staging."""
    _FakeDate.today_value = datetime.date(2026, 9, 29)
    request = MagicMock()
    request.args = {}
    with patch("main.datetime.date", _FakeDate), \
         patch("main.export_firestore", return_value="fs"), \
         patch("main.export_bigquery_tables", return_value={}), \
         patch("main.sync_gcs_to_staging", return_value="gcs") as sync, \
         patch("main.export_secrets", return_value="sec") as secrets, \
         patch("main._run_git_repos_backup", return_value="git"), \
         patch("main.upload_staging_to_s3", return_value="s3"), \
         patch("main.send_alert"):
        main.backup_to_aws(request)
    sources = [c.kwargs["source_bucket"] for c in sync.call_args_list]
    assert sources == ["nomadkaraoke-kn-data"]
    assert all(c.kwargs["staging_prefix"] != "gcs/job-files/" for c in sync.call_args_list)
    secrets.assert_called_once()
