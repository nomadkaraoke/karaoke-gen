"""karaoke-decide's search service and index-build job must be watched by the error monitor."""
from backend.services.error_monitor import config, monitor


def test_decide_search_resources_monitored():
    assert "karaoke-decide-search" in config.MONITORED_CLOUD_RUN_SERVICES
    assert "song-index-build" in config.MONITORED_CLOUD_RUN_JOBS


def test_monitor_log_query_covers_them():
    assert "karaoke-decide-search" in monitor.RESOURCE_TYPE_FILTERS["cloud_run_revision"][1]
    assert "song-index-build" in monitor.RESOURCE_TYPE_FILTERS["cloud_run_job"][1]
