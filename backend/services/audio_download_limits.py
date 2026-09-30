"""Time budgets for the audio-download Cloud Run Job (torrent stall handling).

A torrent whose only seeder is intermittently offline can sit at 0 peers for a
while and then complete in seconds. flacfetch aborts a download after
``max_stall_seconds`` with no progress; the worker then fails the job with
error code ``audio_download_stalled`` and the user chooses "Keep trying" (a
longer stall budget) or different audio — there is no silent auto-retry.

The worker's wait and the Cloud Run execution timeout are derived from the stall
budget so they always leave room for the download itself plus the GCS upload.
"""

# First attempt: wait this long with no progress before asking the user.
STALL_SECONDS_DEFAULT = 20 * 60
# "Keep trying": wait up to another hour (flacfetch caps a request at 3600s).
STALL_SECONDS_KEEP_TRYING = 60 * 60

# Once data flows the stall clock resets; allow this much active download time
# on top of the stall budget before the worker gives up waiting.
_ACTIVE_DOWNLOAD_ALLOWANCE_SECONDS = 20 * 60
# Headroom between the worker's wait and the Cloud Run task timeout, for the GCS
# upload + state transition (so the worker fails gracefully, never SIGKILLed).
_TASK_HEADROOM_SECONDS = 5 * 60

# While waiting on a torrent, the worker writes progress to the job this often.
# Must stay well under the recover-stuck-jobs "no update for 10 min" threshold
# (job_health_service: downloading_audio_stuck), or a live wait gets parked.
HEARTBEAT_INTERVAL_SECONDS = 2 * 60

# Firestore state_data flag set by /retry {keep_trying: true}.
KEEP_TRYING_STATE_KEY = "audio_download_keep_trying"

# error_details.code for a stalled download (read by the job card).
STALLED_ERROR_CODE = "audio_download_stalled"


def stall_seconds(keep_trying: bool) -> int:
    return STALL_SECONDS_KEEP_TRYING if keep_trying else STALL_SECONDS_DEFAULT


def torrent_wait_timeout_seconds(keep_trying: bool) -> int:
    return stall_seconds(keep_trying) + _ACTIVE_DOWNLOAD_ALLOWANCE_SECONDS


def task_timeout_seconds(keep_trying: bool) -> int:
    """Cloud Run execution timeout override for audio-download-job."""
    return torrent_wait_timeout_seconds(keep_trying) + _TASK_HEADROOM_SECONDS
