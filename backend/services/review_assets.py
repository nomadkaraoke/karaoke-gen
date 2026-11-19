"""Everything the review page loads, prepared before the review button appears.

The review page plays OGG transcodes of the input audio and stems, draws the
backing-vocals waveform (``/waveform-data``) and the per-segment vocal waveform
strips (``/vocals-peaks``). Each endpoint can compute its asset on a cache miss,
but that costs the reviewer seconds of waiting and the API instance seconds of
CPU. ``prepare_review_assets`` fills every cache up front.

It runs twice per job: in the screens worker (often before the stems exist, so
only the input-audio assets can be made), and again in
``review_gate.enter_human_review`` right before AWAITING_REVIEW, once the stems
have landed. The second run is mostly cache hits; the stem-based waveforms are
what it adds. Non-fatal throughout: every endpoint still works lazily.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

REVIEW_WAVEFORM_POINTS = 1000
VOCALS_PEAKS_PER_SECOND = 400


async def prepare_review_assets(job_id: str, job_manager=None, job_log: Optional[logging.Logger] = None) -> None:
    """Transcode review audio and pre-compute both review waveforms. Never raises."""
    from backend.services.audio_transcoding_service import AudioTranscodingService
    from backend.services.job_manager import JobManager

    log = job_log or logger
    job_manager = job_manager or JobManager()
    try:
        job = await asyncio.to_thread(job_manager.get_job, job_id)
        if not job:
            log.warning(f"Could not get job {job_id} for review asset preparation")
            return

        # GCS download of up to 4 ~35MB FLACs + an ffmpeg transcode each (7-15s
        # apiece) — this ran ON the event loop and froze the whole instance for
        # 10-45s per job (2026-10-03 analysis). Run it in a worker thread.
        transcoding = AudioTranscodingService()
        try:
            transcoded = await asyncio.to_thread(transcoding.prepare_review_audio_for_job, job)
            log.info(f"Review audio transcoding complete: {len(transcoded)} files")
        except Exception as e:
            # The waveforms below don't depend on it: carry on.
            log.warning(f"Review audio transcoding failed (non-fatal): {e}")

        # The first import pulls in numpy/pydub/matplotlib (seconds of CPU on a
        # fresh instance) — import it in a worker thread, not on the loop.
        def _import_audio_analysis():
            from backend.services.audio_analysis_service import AudioAnalysisService
            return AudioAnalysisService

        AudioAnalysisService = await asyncio.to_thread(_import_audio_analysis)

        # Same source choice as GET /waveform-data: backing vocals, else the input.
        stems = job.file_urls.get("stems", {}) if job.file_urls else {}
        waveform_source = stems.get("backing_vocals") or job.input_media_gcs_path
        if waveform_source:
            try:
                await asyncio.to_thread(
                    AudioAnalysisService().get_review_waveform,
                    waveform_source, job_id, REVIEW_WAVEFORM_POINTS, transcoding,
                )
                log.info("Review waveform data pre-computed")
            except Exception as e:
                log.warning(f"Review waveform pre-compute failed (non-fatal): {e}")

        # Vocal peak envelope for the per-segment waveform strips (needs a stem).
        from backend.utils.stems import vocals_stem_path

        vocals_source = vocals_stem_path(job)
        if vocals_source:
            try:
                await asyncio.to_thread(
                    AudioAnalysisService().get_vocals_peaks,
                    vocals_source, job_id, VOCALS_PEAKS_PER_SECOND, transcoding,
                )
                log.info("Vocals peak envelope pre-computed")
            except Exception as e:
                log.warning(f"Vocals peaks pre-compute failed (non-fatal): {e}")

    except Exception as e:
        log.warning(f"Review asset preparation failed (non-fatal): {e}")
        logger.warning(f"Job {job_id}: Review asset preparation failed: {e}")
