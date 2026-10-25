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


def _api_deploy_step() -> str:
    """ci.yml text of the `gcloud run deploy karaoke-backend` command's step."""
    start = CI.find("gcloud run deploy karaoke-backend")
    assert start != -1, "could not find `gcloud run deploy karaoke-backend` in ci.yml"
    end = CI.find("\n      - name:", start)
    return CI[start:end if end != -1 else len(CI)]


def _ci_flag_list(flag: str) -> set:
    m = re.search(rf'{flag} "([^"]+)"', _api_deploy_step())
    assert m, f"could not find {flag} in the karaoke-backend deploy step of ci.yml"
    return set(m.group(1).split(","))


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


def _job_function_for(job_name: str) -> str:
    tree = ast.parse(CLOUD_RUN)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("create_"):
            src = ast.get_source_segment(CLOUD_RUN, node)
            if f'name="{job_name}"' in src:
                return src
    raise AssertionError(f"no cloud_run.py job function creates {job_name}")


CI_IMAGE_PINNED_JOBS = sorted(set(re.findall(r"gcloud run jobs update ([a-z0-9-]+)", CI)))


def test_ci_pins_the_expected_jobs():
    assert CI_IMAGE_PINNED_JOBS == [
        "audio-separation-job", "lyrics-transcription-job", "storage-retention-job",
        "video-encoding-job", "voice-pool-separate-job",
    ]


@pytest.mark.parametrize("job_name", CI_IMAGE_PINNED_JOBS)
def test_ci_pinned_job_ignores_image_changes(job_name):
    """CI pins these jobs to a SHA image after each deploy; a pulumi up (e.g. a
    targeted one for the push env) must not roll them back to `:latest`."""
    assert "opts=_ci_pinned_image_opts()" in _job_function_for(job_name)


def test_ci_pinned_image_path_is_the_job_container_image():
    assert re.search(
        r'^CI_PINNED_IMAGE_PATH = "template\.template\.containers\[0\]\.image"$', CLOUD_RUN, re.MULTILINE)
    assert "ignore_changes=[CI_PINNED_IMAGE_PATH]" in _function_source("_ci_pinned_image_opts")
