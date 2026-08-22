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


if __name__ == "__main__":
    unittest.main()
