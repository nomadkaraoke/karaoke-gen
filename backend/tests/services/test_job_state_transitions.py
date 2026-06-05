from backend.models.job import JobStatus, STATE_TRANSITIONS


def test_awaiting_duration_confirm_exists():
    assert JobStatus.AWAITING_DURATION_CONFIRM.value == "awaiting_duration_confirm"


def test_can_pause_from_download_and_edit_complete():
    assert JobStatus.AWAITING_DURATION_CONFIRM in STATE_TRANSITIONS[JobStatus.DOWNLOADING]
    assert JobStatus.AWAITING_DURATION_CONFIRM in STATE_TRANSITIONS[JobStatus.AUDIO_EDIT_COMPLETE]
    assert JobStatus.AWAITING_DURATION_CONFIRM in STATE_TRANSITIONS[JobStatus.DOWNLOADING_AUDIO]


def test_resume_targets_processing_or_terminal():
    targets = STATE_TRANSITIONS[JobStatus.AWAITING_DURATION_CONFIRM]
    for t in (JobStatus.SEPARATING_STAGE1, JobStatus.TRANSCRIBING,
              JobStatus.GENERATING_SCREENS, JobStatus.FAILED, JobStatus.CANCELLED):
        assert t in targets
