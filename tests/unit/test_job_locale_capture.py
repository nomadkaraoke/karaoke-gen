"""Tests that a job's submission locale (admin language signal) is persisted.

JobCreate.locale must flow through JobManager.create_job onto the stored Job so
the admin UI can show what language each job was submitted in.
"""
from unittest.mock import MagicMock, patch

from backend.models.job import Job, JobCreate


def _make_manager():
    with patch("backend.services.job_manager.FirestoreService"), \
         patch("backend.services.job_manager.StorageService"):
        from backend.services.job_manager import JobManager
        mgr = JobManager()
    mgr.firestore = MagicMock()
    mgr.storage = MagicMock()
    return mgr


def test_jobcreate_and_job_accept_locale():
    jc = JobCreate(theme_id="nomad", locale="pt")
    assert jc.locale == "pt"
    # Job model round-trips the field.
    assert "locale" in Job.model_fields


def test_create_job_persists_locale_admin_bypass():
    mgr = _make_manager()
    jc = JobCreate(theme_id="nomad", url="https://youtu.be/x", locale="ja")

    # is_admin=True bypasses credit checks/deductions, so no user_service needed.
    job = mgr.create_job(jc, is_admin=True)

    assert job.locale == "ja"
    # The persisted Job (passed to firestore.create_job) carries the locale too.
    saved_job = mgr.firestore.create_job.call_args.args[0]
    assert saved_job.locale == "ja"


def test_create_job_locale_defaults_none():
    mgr = _make_manager()
    jc = JobCreate(theme_id="nomad", url="https://youtu.be/x")
    job = mgr.create_job(jc, is_admin=True)
    assert job.locale is None
