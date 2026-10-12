"""Guards for the encoding-worker boot scripts' gcloud on-GCE cache handling.

A gcloud call made before DHCP finishes writes "False" to /root/.config/gcloud/gce,
so every later gcloud call in that boot has no credentials. The worker then had
no API key, self-heal refused to serve, and systemd re-ran startup.sh against the
same poisoned cache until the VM idle-stopped (jobs f4310aea 2026-10-09,
82f48738 2026-10-12). The scripts must drop that cache before fetching the key.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STARTUP = REPO / "infrastructure" / "encoding-worker" / "startup.sh"
PROVISION = REPO / "infrastructure" / "packer" / "scripts" / "provision.sh"

CACHE_RM = "rm -f /root/.config/gcloud/gce"
SECRET_FETCH = "gcloud secrets versions access latest --secret=encoding-worker-api-key"


def test_startup_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(STARTUP)], check=True)


def test_startup_clears_gce_cache_before_first_secret_fetch():
    text = STARTUP.read_text()
    assert CACHE_RM in text
    assert text.index(CACHE_RM) < text.index(SECRET_FETCH)


def test_startup_waits_for_metadata_server_before_fetch():
    text = STARTUP.read_text()
    metadata_probe = text.index("computeMetadata/v1/instance/id")
    assert metadata_probe < text.index(SECRET_FETCH)


def test_startup_retries_secret_fetch():
    text = STARTUP.read_text()
    loops = re.findall(r"for attempt in \$\(seq 1 \d+\); do\n(.*?)\ndone", text, re.S)
    assert any(SECRET_FETCH in body and CACHE_RM in body for body in loops)


def test_packer_fallback_clears_gce_cache_before_secret_fetch():
    text = PROVISION.read_text()
    fallback = text[text.index("startup-fallback.sh << 'FALLBACK'"):]
    assert CACHE_RM in fallback
    assert fallback.index(CACHE_RM) < fallback.index(SECRET_FETCH)


def test_startup_metadata_probe_has_total_timeout():
    text = STARTUP.read_text()
    lines = text.splitlines()
    url_idx = next(i for i, l in enumerate(lines) if "computeMetadata/v1/instance/id" in l)
    # The curl flags sit on the line before the URL continuation.
    assert "--max-time" in lines[url_idx - 1] + lines[url_idx]


def test_startup_exits_nonzero_without_api_key():
    text = STARTUP.read_text()
    block = text[text.index('if [ -z "$ENCODING_API_KEY" ]; then'):]
    block = block[: block.index("fi")]
    assert "exit 1" in block
