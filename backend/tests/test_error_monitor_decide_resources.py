"""karaoke-decide's search service and index-build job must be watched by the error monitor."""
from backend.services.error_monitor import config, monitor


def test_decide_search_resources_monitored():
    assert "karaoke-decide-search" in config.MONITORED_CLOUD_RUN_SERVICES
    assert "song-index-build" in config.MONITORED_CLOUD_RUN_JOBS


def test_monitor_log_query_covers_them():
    assert "karaoke-decide-search" in monitor.RESOURCE_TYPE_FILTERS["cloud_run_revision"][1]
    assert "song-index-build" in monitor.RESOURCE_TYPE_FILTERS["cloud_run_job"][1]


def test_voice_pool_jobs_monitored_across_regions():
    """voice-pool-* jobs (decide us-central1, gen GPU us-east4) must reach #alerts."""
    jobs = monitor.RESOURCE_TYPE_FILTERS["cloud_run_job"][1]
    for job in ("voice-pool-select", "voice-pool-fetch", "voice-pool-analyse", "voice-pool-separate-job", "vocal-range-gen"):
        assert job in jobs
    # The query filters by job_name only (no region/location), so us-east4 is covered.
    flt = monitor._build_log_filter("cloud_run_job", jobs, 60)
    assert "voice-pool-separate-job" in flt
    assert "location" not in flt
