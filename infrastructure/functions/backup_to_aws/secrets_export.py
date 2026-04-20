"""Secret Manager export to GCS staging bucket.

Enumerates all secrets in the project, fetches the latest version of each,
serialises to JSON, then encrypts with a Curve25519 public key (PyNaCl sealed
box). The matching private key lives only in the user's password manager — the
Cloud Function holds no decryption capability, so a full GCP+AWS compromise
still cannot reveal the secrets.
"""

import base64
import datetime
import json
import logging

from google.cloud import secretmanager
from google.cloud import storage as gcs_storage
from nacl.public import PublicKey, SealedBox

logger = logging.getLogger(__name__)


def _list_secret_ids(client: secretmanager.SecretManagerServiceClient, project: str) -> list[str]:
    parent = f"projects/{project}"
    return [s.name.split("/")[-1] for s in client.list_secrets(request={"parent": parent})]


def _fetch_latest(client: secretmanager.SecretManagerServiceClient, project: str, secret_id: str) -> dict:
    name = f"projects/{project}/secrets/{secret_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
    except Exception as e:
        logger.warning(f"Skipped {secret_id}: {e}")
        return {"error": str(e)}
    payload_bytes = response.payload.data
    try:
        value = payload_bytes.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        value = base64.b64encode(payload_bytes).decode("ascii")
        encoding = "base64"
    return {
        "value": value,
        "encoding": encoding,
        "version_name": response.name,
    }


def export_secrets(
    project: str,
    staging_bucket: str,
    date_str: str,
    public_key_hex: str,
) -> str:
    """Export all secrets, encrypt with sealed box, write to staging bucket.

    Args:
        project: GCP project ID.
        staging_bucket: GCS staging bucket name.
        date_str: ISO date string (YYYY-MM-DD) for the staged object name.
        public_key_hex: 64-char hex string for the Curve25519 public key.

    Returns:
        Summary string.
    """
    if not public_key_hex:
        raise ValueError("BACKUP_ENCRYPTION_PUBKEY is empty — refusing to write unencrypted secrets")

    public_key = PublicKey(bytes.fromhex(public_key_hex))
    sealed_box = SealedBox(public_key)

    sm_client = secretmanager.SecretManagerServiceClient()
    secret_ids = _list_secret_ids(sm_client, project)
    logger.info(f"Found {len(secret_ids)} secrets to back up")

    bundle = {
        "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
        "project": project,
        "secrets": {sid: _fetch_latest(sm_client, project, sid) for sid in secret_ids},
    }

    plaintext = json.dumps(bundle, sort_keys=True).encode("utf-8")
    ciphertext = sealed_box.encrypt(plaintext)

    gcs_client = gcs_storage.Client()
    blob = gcs_client.bucket(staging_bucket).blob(f"secrets/{date_str}.bin")
    blob.upload_from_string(ciphertext, content_type="application/octet-stream")

    summary = f"Encrypted {len(secret_ids)} secrets ({len(plaintext)} → {len(ciphertext)} bytes) to gs://{staging_bucket}/secrets/{date_str}.bin"
    logger.info(summary)
    return summary
