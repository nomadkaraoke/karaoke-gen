"""Tests for s3_upload module, focused on the exclude_prefixes behavior."""

import unittest
from unittest.mock import MagicMock, patch


def _blob(name):
    b = MagicMock()
    b.name = name
    return b


class TestUploadStagingToS3(unittest.TestCase):
    @patch("s3_upload.boto3.client")
    @patch("s3_upload.storage.Client")
    @patch("s3_upload.get_aws_credentials")
    def test_excluded_prefix_not_uploaded_or_deleted(self, mock_creds, mock_storage, mock_boto):
        from s3_upload import upload_staging_to_s3

        mock_creds.return_value = {"access_key_id": "x", "secret_access_key": "y"}
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
    def test_small_irreplaceable_prefixes_upload_before_large_finals(
        self, mock_creds, mock_storage, mock_boto
    ):
        """Regression (DR incident 2026-09): a mid-run timeout must not starve
        secrets/git-repos. The huge gcs/job-files/ prefix must upload LAST even
        though it sorts before git-repos/ and secrets/ lexicographically."""
        from s3_upload import upload_staging_to_s3

        mock_creds.return_value = {"access_key_id": "x", "secret_access_key": "y"}
        # Deliberately in lexicographic (list_blobs) order, which is the buggy order.
        blobs = [
            _blob("firestore/2026-09-14/out.bin"),
            _blob("gcs/job-files/jobs/abc/finals/huge.mp4"),
            _blob("gcs/kn-data/index.json"),
            _blob("git-repos/nomadkaraoke/karaoke-gen.bundle"),
            _blob("secrets/2026-09-14.bin"),
        ]
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = blobs

        uploaded_keys = []
        mock_boto.return_value.upload_fileobj.side_effect = (
            lambda Fileobj, Bucket, Key: uploaded_keys.append(Key)
        )

        upload_staging_to_s3("staging", "s3b")

        # secrets + git-repos must both precede the large gcs/job-files/ final.
        job_files_idx = next(i for i, k in enumerate(uploaded_keys) if k.startswith("gcs/job-files/"))
        secrets_idx = next(i for i, k in enumerate(uploaded_keys) if k.startswith("secrets/"))
        git_idx = next(i for i, k in enumerate(uploaded_keys) if k.startswith("git-repos/"))
        self.assertLess(secrets_idx, job_files_idx)
        self.assertLess(git_idx, job_files_idx)
        # gcs/job-files/ is the largest — always uploaded last.
        self.assertEqual(job_files_idx, len(uploaded_keys) - 1)


if __name__ == "__main__":
    unittest.main()
