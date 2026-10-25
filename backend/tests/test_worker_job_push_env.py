"""Worker Cloud Run Jobs carry the API service's Web Push config.

PushNotificationService.is_enabled() needs ENABLE_PUSH_NOTIFICATIONS=true and the
VAPID keys. CI sets them on the API service only (`gcloud run deploy` in ci.yml);
the Pulumi job definitions had none, so every Web Push a worker job triggered
(failure, audio edit, "video ready") was silently skipped. Source-level checks,
like test_worker_service_cpu_jobs_region: infrastructure/ isn't importable here.
"""
import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLOUD_RUN = (REPO_ROOT / "infrastructure" / "modules" / "cloud_run.py").read_text()
CI = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

# Jobs whose workers change job state (and so notify), with the worker each runs.
NOTIFYING_JOBS = {
    "create_lyrics_transcription_job": "backend.workers.lyrics_worker",
    "create_audio_separation_job": "backend.workers.audio_worker",
    "create_audio_download_job": "backend.workers.audio_download_worker",
    "create_bulk_search_job": "backend.workers.bulk_search_worker",
    # also runs backend.workers.render_video_worker via an args override
    "create_video_encoding_job": "backend.workers.video_worker",
}


def _function_source(name: str) -> str:
    tree = ast.parse(CLOUD_RUN)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(CLOUD_RUN, node)
    raise AssertionError(f"{name} not found in infrastructure/modules/cloud_run.py")


def _push_secrets() -> dict:
    tree = ast.parse(CLOUD_RUN)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PUSH_NOTIFICATION_SECRETS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("PUSH_NOTIFICATION_SECRETS not found in cloud_run.py")


def _ci_flag_list(flag: str) -> set:
    # The API service deploy is the longest such list (comments quote short ones).
    found = [m.group(1) for m in re.finditer(rf'{flag} "([^"]+)"', CI)]
    assert found, f"could not find the API deploy's {flag} in ci.yml"
    return set(max(found, key=len).split(","))


@pytest.mark.parametrize("job_fn,worker", sorted(NOTIFYING_JOBS.items()))
def test_notifying_job_gets_push_envs(job_fn, worker):
    src = _function_source(job_fn)
    assert worker in src, f"{job_fn} no longer runs {worker}"
    assert "*_push_notification_envs()" in src, f"{job_fn} lacks the Web Push env (ENABLE_PUSH_NOTIFICATIONS + VAPID keys)"


def test_push_envs_enable_push_and_mount_vapid_secrets():
    src = _function_source("_push_notification_envs")
    assert re.search(r'name="ENABLE_PUSH_NOTIFICATIONS",\s*value="true"', src)
    assert "secret_key_ref" in src and 'version="latest"' in src
    # push_notification_service reads these via settings.get_secret, env first.
    assert _push_secrets() == {"VAPID_PUBLIC_KEY": "vapid-public-key", "VAPID_PRIVATE_KEY": "vapid-private-key"}


def test_job_push_config_matches_the_api_service_deploy():
    assert "ENABLE_PUSH_NOTIFICATIONS=true" in _ci_flag_list("--set-env-vars")
    api_secrets = _ci_flag_list("--set-secrets")
    for env_name, secret_id in _push_secrets().items():
        assert f"{env_name}={secret_id}:latest" in api_secrets
