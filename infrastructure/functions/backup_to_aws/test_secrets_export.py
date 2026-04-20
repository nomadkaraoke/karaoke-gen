"""Tests for secrets_export module."""

import json
from unittest.mock import MagicMock, patch

import pytest
from nacl.public import PrivateKey, SealedBox

from secrets_export import export_secrets


@pytest.fixture
def keypair():
    sk = PrivateKey.generate()
    return sk, sk.public_key.encode().hex()


def _fake_sm_client(secrets: dict[str, bytes]):
    client = MagicMock()
    client.list_secrets.return_value = [
        MagicMock(name=f"projects/test/secrets/{sid}") for sid in secrets
    ]
    # MagicMock 'name' attribute isn't auto-assigned via constructor kwarg; set explicitly
    for mock_obj, sid in zip(client.list_secrets.return_value, secrets):
        mock_obj.name = f"projects/test/secrets/{sid}"

    def access(request):
        secret_id = request["name"].split("/")[3]
        resp = MagicMock()
        resp.payload.data = secrets[secret_id]
        resp.name = f"projects/test/secrets/{secret_id}/versions/1"
        return resp

    client.access_secret_version.side_effect = access
    return client


def test_round_trip_decrypts_with_private_key(keypair):
    sk, pk_hex = keypair
    fake_secrets = {
        "stripe-secret-key": b"sk_test_abcdef",
        "spotipy-client-secret": b"client_secret_xyz",
    }
    captured_blob = {}

    def fake_upload(data, content_type):
        captured_blob["data"] = data
        captured_blob["content_type"] = content_type

    fake_blob = MagicMock()
    fake_blob.upload_from_string.side_effect = fake_upload
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_gcs = MagicMock()
    fake_gcs.bucket.return_value = fake_bucket

    with patch("secrets_export.secretmanager.SecretManagerServiceClient", return_value=_fake_sm_client(fake_secrets)), \
         patch("secrets_export.gcs_storage.Client", return_value=fake_gcs):
        summary = export_secrets(
            project="test",
            staging_bucket="staging",
            date_str="2026-04-20",
            public_key_hex=pk_hex,
        )

    assert "2 secrets" in summary
    assert captured_blob["content_type"] == "application/octet-stream"

    # Decrypt with the private key (this is what the recovery procedure does)
    plaintext = SealedBox(sk).decrypt(captured_blob["data"])
    bundle = json.loads(plaintext.decode("utf-8"))
    assert bundle["project"] == "test"
    assert bundle["secrets"]["stripe-secret-key"]["value"] == "sk_test_abcdef"
    assert bundle["secrets"]["stripe-secret-key"]["encoding"] == "utf-8"
    assert bundle["secrets"]["spotipy-client-secret"]["value"] == "client_secret_xyz"


def test_binary_secret_is_base64_encoded(keypair):
    sk, pk_hex = keypair
    binary_payload = b"\x00\x01\x02\xff"
    fake_secrets = {"binary-key": binary_payload}
    captured = {}

    fake_blob = MagicMock()
    fake_blob.upload_from_string.side_effect = lambda data, content_type: captured.update(data=data)
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_gcs = MagicMock()
    fake_gcs.bucket.return_value = fake_bucket

    with patch("secrets_export.secretmanager.SecretManagerServiceClient", return_value=_fake_sm_client(fake_secrets)), \
         patch("secrets_export.gcs_storage.Client", return_value=fake_gcs):
        export_secrets("test", "staging", "2026-04-20", pk_hex)

    bundle = json.loads(SealedBox(sk).decrypt(captured["data"]))
    entry = bundle["secrets"]["binary-key"]
    assert entry["encoding"] == "base64"
    import base64
    assert base64.b64decode(entry["value"]) == binary_payload


def test_empty_pubkey_raises():
    with pytest.raises(ValueError, match="BACKUP_ENCRYPTION_PUBKEY"):
        export_secrets("test", "staging", "2026-04-20", "")


def test_individual_fetch_failure_does_not_abort(keypair):
    sk, pk_hex = keypair
    sm_client = MagicMock()
    s1 = MagicMock(); s1.name = "projects/test/secrets/works"
    s2 = MagicMock(); s2.name = "projects/test/secrets/broken"
    sm_client.list_secrets.return_value = [s1, s2]

    def access(request):
        if "broken" in request["name"]:
            raise PermissionError("denied")
        resp = MagicMock()
        resp.payload.data = b"ok"
        resp.name = "projects/test/secrets/works/versions/1"
        return resp

    sm_client.access_secret_version.side_effect = access
    captured = {}
    fake_blob = MagicMock()
    fake_blob.upload_from_string.side_effect = lambda data, content_type: captured.update(data=data)
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_gcs = MagicMock()
    fake_gcs.bucket.return_value = fake_bucket

    with patch("secrets_export.secretmanager.SecretManagerServiceClient", return_value=sm_client), \
         patch("secrets_export.gcs_storage.Client", return_value=fake_gcs):
        export_secrets("test", "staging", "2026-04-20", pk_hex)

    bundle = json.loads(SealedBox(sk).decrypt(captured["data"]))
    assert bundle["secrets"]["works"]["value"] == "ok"
    assert "error" in bundle["secrets"]["broken"]
