"""Tests for s3_upload: exclude/dedupe/idempotency/ordering behavior."""

import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

_NOT_FOUND = ClientError({"Error": {"Code": "404"}}, "HeadObject")


def _blob(name, size=1234, crc32c=None):
    b = MagicMock()
    b.name = name
    b.size = size
    b.crc32c = crc32c if crc32c is not None else f"crc-{name}"
    b.md5_hash = None
    # blob.open("rb") is used as a context manager.
    b.open.return_value.__enter__.return_value = MagicMock()
    return b


def _mock_boto(mock_boto):
    """Default: nothing exists in S3 yet (HEAD -> 404), so everything uploads."""
    client = mock_boto.return_value
    client.head_object.side_effect = _NOT_FOUND
    return client


class TestUploadStagingToS3(unittest.TestCase):
    @patch("s3_upload.boto3.client")
    @patch("s3_upload.storage.Client")
    @patch("s3_upload.get_aws_credentials")
    def test_excluded_prefix_not_uploaded_or_deleted(self, mock_creds, mock_storage, mock_boto):
        from s3_upload import upload_staging_to_s3

        mock_creds.return_value = {"access_key_id": "x", "secret_access_key": "y"}
        _mock_boto(mock_boto)
        fs = _blob("firestore/2026-03-29/out.bin")
        sec = _blob("secrets/2026-03-29.bin")
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = [fs, sec]

        upload_staging_to_s3("staging", "s3b", exclude_prefixes=["firestore/"])

        # firestore held back: not uploaded, not deleted (stays as local backup)
        fs.delete.assert_not_called()
        # secrets uploaded then deleted
        sec.delete.assert_called_once()
        self.assertEqual(mock_boto.return_value.upload_fileobj.call_count, 1)

    @patch("s3_upload.boto3.client")
    @patch("s3_upload.storage.Client")
    @patch("s3_upload.get_aws_credentials")
    def test_no_exclude_uploads_everything(self, mock_creds, mock_storage, mock_boto):
        from s3_upload import upload_staging_to_s3

        mock_creds.return_value = {"access_key_id": "x", "secret_access_key": "y"}
        _mock_boto(mock_boto)
        fs = _blob("firestore/2026-03-29/out.bin")
        sec = _blob("secrets/2026-03-29.bin")
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = [fs, sec]

        upload_staging_to_s3("staging", "s3b")

        self.assertEqual(mock_boto.return_value.upload_fileobj.call_count, 2)
        fs.delete.assert_called_once()
        sec.delete.assert_called_once()

    @patch("s3_upload.boto3.client")
    @patch("s3_upload.storage.Client")
    @patch("s3_upload.get_aws_credentials")
    def test_byte_identical_final_duplicate_is_deduped(self, mock_creds, mock_storage, mock_boto):
        """The machine-named and human-named copies of a final are byte-identical
        (same crc32c) and live in the same finals/ dir — only one should upload."""
        from s3_upload import upload_staging_to_s3

        mock_creds.return_value = {"access_key_id": "x", "secret_access_key": "y"}
        _mock_boto(mock_boto)
        machine = _blob("gcs/job-files/jobs/abc/finals/lossless_4k_mp4.mp4", crc32c="AAAA")
        human = _blob(
            "gcs/job-files/jobs/abc/finals/Artist - Title (Final Karaoke Lossless 4k).mp4",
            crc32c="AAAA",
        )
        # A genuinely different final in the same dir must survive.
        other = _blob("gcs/job-files/jobs/abc/finals/portrait_1080x1920.mp4", crc32c="BBBB")
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = [machine, human, other]

        upload_staging_to_s3("staging", "s3b")

        # One of the identical twins is deleted without upload; the other + the
        # distinct final are uploaded. Two distinct contents -> two uploads.
        self.assertEqual(mock_boto.return_value.upload_fileobj.call_count, 2)
        # Every blob is removed from staging (dup via dedupe, others via upload).
        for b in (machine, human, other):
            b.delete.assert_called_once()

    @patch("s3_upload.boto3.client")
    @patch("s3_upload.storage.Client")
    @patch("s3_upload.get_aws_credentials")
    def test_already_in_s3_is_skipped_not_reuploaded(self, mock_creds, mock_storage, mock_boto):
        """Idempotency: an object already in S3 with the same size is skipped
        (deleted from staging) rather than re-transferred."""
        from s3_upload import upload_staging_to_s3

        mock_creds.return_value = {"access_key_id": "x", "secret_access_key": "y"}
        client = mock_boto.return_value
        present = _blob("gcs/job-files/jobs/abc/finals/portrait_1080x1920.mp4", size=999)
        fresh = _blob("secrets/2026-09-15.bin", size=42)

        def head(Bucket, Key):
            if Key == present.name:
                return {"ContentLength": 999}
            raise _NOT_FOUND

        client.head_object.side_effect = head
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = [present, fresh]

        upload_staging_to_s3("staging", "s3b")

        # Only the not-yet-present object is uploaded; both are cleared from staging.
        self.assertEqual(client.upload_fileobj.call_count, 1)
        self.assertEqual(client.upload_fileobj.call_args.kwargs["Key"], fresh.name)
        present.delete.assert_called_once()
        fresh.delete.assert_called_once()

    @patch("s3_upload.ThreadPoolExecutor")
    @patch("s3_upload.boto3.client")
    @patch("s3_upload.storage.Client")
    @patch("s3_upload.get_aws_credentials")
    def test_critical_prefixes_complete_before_large_finals(
        self, mock_creds, mock_storage, mock_boto, mock_pool
    ):
        """Ordering barrier: the small/irreplaceable phase must run to completion
        before the large gcs/job-files/ phase starts."""
        from s3_upload import upload_staging_to_s3

        mock_creds.return_value = {"access_key_id": "x", "secret_access_key": "y"}
        _mock_boto(mock_boto)

        # Record which blob names each ThreadPoolExecutor.map() phase received.
        phases = []

        class FakePool:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def map(self, fn, blobs):
                blobs = list(blobs)
                phases.append([b.name for b in blobs])
                return [fn(b) for b in blobs]

        mock_pool.return_value = FakePool()

        sec = _blob("secrets/2026-09-15.bin")
        git = _blob("git-repos/nomadkaraoke/karaoke-gen.bundle")
        final = _blob("gcs/job-files/jobs/abc/finals/lossless_4k_mp4.mp4")
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = [final, sec, git]

        upload_staging_to_s3("staging", "s3b")

        self.assertEqual(len(phases), 2, "expected a critical phase then a bulk phase")
        critical_phase, bulk_phase = phases
        self.assertCountEqual(critical_phase, [sec.name, git.name])
        self.assertEqual(bulk_phase, [final.name])


if __name__ == "__main__":
    unittest.main()
