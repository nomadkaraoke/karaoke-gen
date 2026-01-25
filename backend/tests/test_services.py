"""
Unit tests for service layer.

Tests AuthService, StorageService, WorkerService, and FirestoreService
without requiring actual cloud resources.
"""
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch, call
from datetime import datetime, UTC

# Mock Google Cloud before imports
import sys
sys.modules['google.cloud.firestore'] = MagicMock()
sys.modules['google.cloud.storage'] = MagicMock()
sys.modules['google.cloud.tasks_v2'] = MagicMock()

from backend.services.auth_service import AuthService, UserType
from backend.services.storage_service import StorageService  
from backend.services.worker_service import WorkerService, get_worker_service
from backend.services.firestore_service import FirestoreService
from backend.models.job import Job, JobStatus


class TestAuthService:
    """Test AuthService token validation and management."""
    
    def test_validate_admin_token(self):
        """Test validating hardcoded admin tokens."""
        with patch('backend.services.auth_service.get_settings') as mock_settings:
            mock_settings.return_value.admin_tokens = "admin123,secret456"
            
            auth_service = AuthService()
            
            # Valid admin token - returns (is_valid, user_type, usage_count, token)
            is_valid, user_type, usage_count, token = auth_service.validate_token('admin123')
            assert is_valid is True
            assert user_type == UserType.ADMIN
            
            # Another valid admin token
            is_valid, user_type, _, _ = auth_service.validate_token('secret456')
            assert is_valid is True
            assert user_type == UserType.ADMIN
    
    def test_validate_invalid_token(self):
        """Test rejecting invalid tokens."""
        with patch('backend.services.auth_service.FirestoreService') as mock_fs:
            mock_fs_instance = Mock()
            mock_fs.return_value = mock_fs_instance
            mock_fs_instance.get_token.return_value = None  # Token not in DB
            
            with patch('backend.services.auth_service.get_settings') as mock_settings:
                mock_settings.return_value.admin_tokens = "admin123"
                
                auth_service = AuthService()
                is_valid, _, _, _ = auth_service.validate_token('invalid_token')
                
                assert is_valid is False
    
    def test_validate_token_from_firestore(self):
        """Test validating tokens stored in Firestore."""
        with patch('backend.services.auth_service.FirestoreService') as mock_fs:
            mock_fs_instance = Mock()
            mock_fs.return_value = mock_fs_instance
            
            # Mock Firestore returning a token
            mock_fs_instance.get_token.return_value = {
                'token': 'db_token123',
                'type': 'unlimited',
                'valid': True,
                'usage_count': 5,
                'created_at': datetime.now(UTC).isoformat()
            }
            
            with patch('backend.services.auth_service.get_settings') as mock_settings:
                mock_settings.return_value.admin_tokens = ""
                
                auth_service = AuthService()
                is_valid, user_type, usage_count, token = auth_service.validate_token('db_token123')
                
                assert is_valid is True
                assert user_type == UserType.UNLIMITED
                # usage_count can be -1 for unlimited tokens
                assert isinstance(usage_count, int)


class TestStorageService:
    """Test StorageService GCS operations."""
    
    def test_upload_file(self):
        """Test uploading a file to GCS."""
        with patch('backend.services.storage_service.storage') as mock_storage:
            mock_client = MagicMock()
            mock_storage.Client.return_value = mock_client
            
            mock_bucket = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            
            mock_blob = MagicMock()
            mock_bucket.blob.return_value = mock_blob
            
            storage_service = StorageService()
            
            # Upload file - correct parameter names
            result = storage_service.upload_file(
                local_path="/tmp/test.flac",
                destination_path="uploads/test123/test.flac"
            )
            
            # Verify blob was created and uploaded
            mock_bucket.blob.assert_called_once_with("uploads/test123/test.flac")
            mock_blob.upload_from_filename.assert_called_once_with("/tmp/test.flac")
            # Result should be the destination path
            assert result == "uploads/test123/test.flac"
    
    def test_download_file(self):
        """Test downloading a file from GCS."""
        with patch('backend.services.storage_service.storage') as mock_storage:
            mock_client = MagicMock()
            mock_storage.Client.return_value = mock_client
            
            mock_bucket = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            
            mock_blob = MagicMock()
            mock_bucket.blob.return_value = mock_blob
            
            storage_service = StorageService()
            
            # Download file - correct parameter names
            storage_service.download_file(
                source_path="uploads/test123/test.flac",
                destination_path="/tmp/downloaded.flac"
            )
            
            # Verify download was called
            mock_blob.download_to_filename.assert_called_once_with("/tmp/downloaded.flac")
    
    def test_delete_file(self):
        """Test deleting a file from GCS."""
        with patch('backend.services.storage_service.storage') as mock_storage:
            mock_client = MagicMock()
            mock_storage.Client.return_value = mock_client
            
            mock_bucket = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            
            mock_blob = MagicMock()
            mock_bucket.blob.return_value = mock_blob
            
            storage_service = StorageService()
            
            # Delete file
            storage_service.delete_file("uploads/test123/test.flac")
            
            # Verify delete was called
            mock_blob.delete.assert_called_once()


class TestWorkerService:
    """Test WorkerService worker triggering."""

    @pytest.mark.asyncio
    async def test_trigger_audio_worker(self):
        """Test triggering audio worker via Cloud Run Jobs."""
        from backend.services.worker_service import WorkerService, reset_worker_service
        reset_worker_service()

        with patch('backend.services.worker_service.get_settings') as mock_settings:
            mock_settings.return_value.admin_tokens = "test-token"
            mock_settings.return_value.google_cloud_project = "test-project"
            mock_settings.return_value.enable_cloud_tasks = False
            mock_settings.return_value.gcp_region = "us-central1"
            mock_settings.return_value.use_cloud_run_jobs_for_video = False

            # Mock Cloud Run v2 Jobs client
            mock_jobs_client = MagicMock()
            mock_operation = MagicMock()
            mock_operation.metadata = "test-metadata"
            mock_jobs_client.run_job.return_value = mock_operation

            mock_run_v2 = MagicMock()
            mock_run_v2.JobsClient.return_value = mock_jobs_client
            mock_run_v2.RunJobRequest = MagicMock()
            mock_run_v2.RunJobRequest.Overrides = MagicMock()
            mock_run_v2.RunJobRequest.Overrides.ContainerOverride = MagicMock()

            with patch.dict('sys.modules', {'google.cloud.run_v2': mock_run_v2}):
                service = WorkerService()
                result = await service.trigger_audio_worker("test123")

                # Verify Cloud Run Job was triggered
                assert result is True
                mock_jobs_client.run_job.assert_called_once()

                # Verify correct job name was used
                call_args = mock_run_v2.RunJobRequest.call_args
                assert call_args is not None
                assert "audio-separation-job" in str(call_args)

    @pytest.mark.asyncio
    async def test_trigger_lyrics_worker(self):
        """Test triggering lyrics worker via Cloud Run Jobs."""
        from backend.services.worker_service import WorkerService, reset_worker_service
        reset_worker_service()

        with patch('backend.services.worker_service.get_settings') as mock_settings:
            mock_settings.return_value.admin_tokens = "test-token"
            mock_settings.return_value.google_cloud_project = "test-project"
            mock_settings.return_value.enable_cloud_tasks = False
            mock_settings.return_value.gcp_region = "us-central1"
            mock_settings.return_value.use_cloud_run_jobs_for_video = False

            # Mock Cloud Run v2 Jobs client
            mock_jobs_client = MagicMock()
            mock_operation = MagicMock()
            mock_operation.metadata = "test-metadata"
            mock_jobs_client.run_job.return_value = mock_operation

            mock_run_v2 = MagicMock()
            mock_run_v2.JobsClient.return_value = mock_jobs_client
            mock_run_v2.RunJobRequest = MagicMock()
            mock_run_v2.RunJobRequest.Overrides = MagicMock()
            mock_run_v2.RunJobRequest.Overrides.ContainerOverride = MagicMock()

            with patch.dict('sys.modules', {'google.cloud.run_v2': mock_run_v2}):
                service = WorkerService()
                result = await service.trigger_lyrics_worker("test123")

                # Verify Cloud Run Job was triggered
                assert result is True
                mock_jobs_client.run_job.assert_called_once()

                # Verify correct job name was used
                call_args = mock_run_v2.RunJobRequest.call_args
                assert call_args is not None
                assert "lyrics-transcription-job" in str(call_args)
    
    @pytest.mark.asyncio
    async def test_trigger_screens_worker(self):
        """Test triggering screens worker via internal HTTP."""
        with patch('backend.services.worker_service.httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            
            mock_response = Mock()
            mock_response.status_code = 200
            mock_client.post.return_value = mock_response
            
            worker_service = get_worker_service()
            
            await worker_service.trigger_screens_worker("test123")
            
            # Verify HTTP POST was made
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "/api/internal/workers/screens" in str(call_args)
    
    @pytest.mark.asyncio
    async def test_trigger_video_worker(self):
        """Test triggering video worker via internal HTTP."""
        with patch('backend.services.worker_service.httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            
            mock_response = Mock()
            mock_response.status_code = 200
            mock_client.post.return_value = mock_response
            
            worker_service = get_worker_service()
            
            await worker_service.trigger_video_worker("test123")
            
            # Verify HTTP POST was made
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "/api/internal/workers/video" in str(call_args)


class TestWorkerServiceCloudTasks:
    """Test WorkerService Cloud Tasks integration."""
    
    def test_should_use_cloud_tasks_default_false(self):
        """Test that Cloud Tasks is disabled by default."""
        from backend.services.worker_service import WorkerService, reset_worker_service
        reset_worker_service()
        
        with patch('backend.services.worker_service.os.getenv') as mock_getenv:
            mock_getenv.side_effect = lambda k, d=None: {
                'PORT': '8000',
            }.get(k, d)
            
            with patch('backend.services.worker_service.get_settings') as mock_settings:
                mock_settings.return_value.admin_tokens = None
                mock_settings.return_value.google_cloud_project = "test-project"
                mock_settings.return_value.enable_cloud_tasks = False
                mock_settings.return_value.gcp_region = "us-central1"
                mock_settings.return_value.use_cloud_run_jobs_for_video = False
                
                service = WorkerService()
                assert service._use_cloud_tasks is False
    
    def test_should_use_cloud_tasks_enabled(self):
        """Test that Cloud Tasks can be enabled via settings."""
        from backend.services.worker_service import WorkerService, reset_worker_service
        reset_worker_service()
        
        with patch('backend.services.worker_service.os.getenv') as mock_getenv:
            mock_getenv.side_effect = lambda k, d=None: {
                'PORT': '8000',
            }.get(k, d)
            
            with patch('backend.services.worker_service.get_settings') as mock_settings:
                mock_settings.return_value.admin_tokens = None
                mock_settings.return_value.google_cloud_project = "test-project"
                mock_settings.return_value.enable_cloud_tasks = True
                mock_settings.return_value.gcp_region = "us-central1"
                mock_settings.return_value.use_cloud_run_jobs_for_video = False
                
                service = WorkerService()
                assert service._use_cloud_tasks is True
    
    def test_worker_queues_mapping(self):
        """Test that all worker types have queue mappings."""
        from backend.services.worker_service import WORKER_QUEUES
        
        # Verify all expected workers have queues
        assert "audio" in WORKER_QUEUES
        assert "lyrics" in WORKER_QUEUES
        assert "screens" in WORKER_QUEUES
        assert "render-video" in WORKER_QUEUES
        assert "video" in WORKER_QUEUES
        
        # Verify queue names are correct
        assert WORKER_QUEUES["audio"] == "audio-worker-queue"
        assert WORKER_QUEUES["lyrics"] == "lyrics-worker-queue"
        assert WORKER_QUEUES["screens"] == "screens-worker-queue"
        assert WORKER_QUEUES["render-video"] == "render-worker-queue"
        assert WORKER_QUEUES["video"] == "video-worker-queue"
    
    @pytest.mark.asyncio
    async def test_trigger_worker_uses_http_when_cloud_tasks_disabled(self):
        """Test that trigger_worker uses HTTP when Cloud Tasks is disabled."""
        from backend.services.worker_service import WorkerService, reset_worker_service
        reset_worker_service()
        
        with patch('backend.services.worker_service.os.getenv') as mock_getenv:
            mock_getenv.side_effect = lambda k, d=None: {
                'PORT': '8000',
            }.get(k, d)
            
            with patch('backend.services.worker_service.get_settings') as mock_settings:
                mock_settings.return_value.admin_tokens = "test-token"
                mock_settings.return_value.google_cloud_project = "test-project"
                mock_settings.return_value.enable_cloud_tasks = False
                mock_settings.return_value.gcp_region = "us-central1"
                mock_settings.return_value.use_cloud_run_jobs_for_video = False
                
                with patch('backend.services.worker_service.httpx.AsyncClient') as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client_cls.return_value.__aenter__.return_value = mock_client
                    
                    mock_response = Mock()
                    mock_response.status_code = 200
                    mock_client.post.return_value = mock_response
                    
                    service = WorkerService()
                    assert service._use_cloud_tasks is False
                    
                    result = await service.trigger_worker("audio", "test-job-123")
                    
                    # Verify HTTP was used
                    assert result is True
                    mock_client.post.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_trigger_worker_uses_cloud_tasks_when_enabled(self):
        """Test that trigger_worker uses Cloud Tasks when enabled."""
        from backend.services.worker_service import WorkerService, reset_worker_service
        reset_worker_service()
        
        with patch('backend.services.worker_service.os.getenv') as mock_getenv:
            mock_getenv.side_effect = lambda k, d=None: {
                'CLOUD_RUN_SERVICE_URL': 'https://api.example.com',
            }.get(k, d)
            
            with patch('backend.services.worker_service.get_settings') as mock_settings:
                mock_settings.return_value.admin_tokens = "test-token"
                mock_settings.return_value.google_cloud_project = "test-project"
                mock_settings.return_value.enable_cloud_tasks = True
                mock_settings.return_value.gcp_region = "us-central1"
                mock_settings.return_value.use_cloud_run_jobs_for_video = False
                
                # Mock Cloud Tasks client
                mock_tasks_client = MagicMock()
                mock_tasks_client.queue_path.return_value = "projects/test-project/locations/us-central1/queues/audio-worker-queue"
                mock_task_response = MagicMock()
                mock_task_response.name = "projects/test-project/locations/us-central1/queues/audio-worker-queue/tasks/abc123"
                mock_tasks_client.create_task.return_value = mock_task_response
                
                # Mock the google.cloud.tasks_v2 module which is imported inside the method
                mock_tasks_module = MagicMock()
                mock_tasks_module.CloudTasksClient.return_value = mock_tasks_client
                mock_tasks_module.HttpMethod.POST = "POST"
                
                with patch.dict('sys.modules', {'google.cloud.tasks_v2': mock_tasks_module}):
                    with patch.dict(sys.modules, {'google.cloud': MagicMock()}):
                        service = WorkerService()
                        service._tasks_client = mock_tasks_client  # Inject mocked client
                        
                        result = await service.trigger_worker("audio", "test-job-123")
                        
                        # Verify Cloud Tasks was used
                        assert result is True
                        mock_tasks_client.create_task.assert_called_once()
                        
                        # Verify task payload
                        call_args = mock_tasks_client.create_task.call_args
                        assert call_args is not None
    
    @pytest.mark.asyncio
    async def test_trigger_worker_returns_false_for_unknown_worker_type(self):
        """Test that trigger_worker returns False for unknown worker types."""
        from backend.services.worker_service import WorkerService, reset_worker_service
        reset_worker_service()
        
        with patch('backend.services.worker_service.os.getenv') as mock_getenv:
            mock_getenv.side_effect = lambda k, d=None: {
                'CLOUD_RUN_SERVICE_URL': 'https://api.example.com',
            }.get(k, d)
            
            with patch('backend.services.worker_service.get_settings') as mock_settings:
                mock_settings.return_value.admin_tokens = "test-token"
                mock_settings.return_value.google_cloud_project = "test-project"
                mock_settings.return_value.enable_cloud_tasks = True
                mock_settings.return_value.gcp_region = "us-central1"
                mock_settings.return_value.use_cloud_run_jobs_for_video = False
                
                service = WorkerService()
                
                result = await service.trigger_worker("unknown-worker", "test-job-123")
                
                assert result is False
    
    def test_reset_worker_service(self):
        """Test that reset_worker_service resets the singleton."""
        from backend.services.worker_service import get_worker_service, reset_worker_service
        
        with patch('backend.services.worker_service.get_settings') as mock_settings:
            mock_settings.return_value.admin_tokens = None
            mock_settings.return_value.google_cloud_project = "test-project"
            mock_settings.return_value.enable_cloud_tasks = False
            mock_settings.return_value.gcp_region = "us-central1"
            mock_settings.return_value.use_cloud_run_jobs_for_video = False
            
            service1 = get_worker_service()
            reset_worker_service()
            service2 = get_worker_service()
            
            # After reset, should be different instances
            assert service1 is not service2


class TestFirestoreService:
    """Test FirestoreService database operations."""
    
    def test_create_job(self):
        """Test creating a job document in Firestore."""
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_client = MagicMock()
            mock_firestore.Client.return_value = mock_client
            
            mock_collection = MagicMock()
            mock_client.collection.return_value = mock_collection
            
            mock_doc_ref = MagicMock()
            mock_collection.document.return_value = mock_doc_ref
            
            firestore_service = FirestoreService()
            
            job = Job(
                job_id="test123",
                status=JobStatus.PENDING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC)
            )
            
            firestore_service.create_job(job)  # Returns None
            
            # Verify document was set
            mock_doc_ref.set.assert_called_once()
    
    def test_get_job(self):
        """Test fetching a job from Firestore."""
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_client = MagicMock()
            mock_firestore.Client.return_value = mock_client
            
            mock_collection = MagicMock()
            mock_client.collection.return_value = mock_collection
            
            mock_doc_ref = MagicMock()
            mock_collection.document.return_value = mock_doc_ref
            
            mock_doc = MagicMock()
            mock_doc.exists = True
            mock_doc.to_dict.return_value = {
                'job_id': 'test123',
                'status': 'pending',
                'progress': 0,
                'created_at': datetime.now(UTC).isoformat(),
                'updated_at': datetime.now(UTC).isoformat()
            }
            mock_doc_ref.get.return_value = mock_doc
            
            firestore_service = FirestoreService()
            
            job = firestore_service.get_job("test123")
            
            # Verify document was fetched
            mock_doc_ref.get.assert_called_once()
            assert job is not None
            assert job.job_id == "test123"
    
    def test_get_nonexistent_job(self):
        """Test fetching a job that doesn't exist."""
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_client = MagicMock()
            mock_firestore.Client.return_value = mock_client
            
            mock_collection = MagicMock()
            mock_client.collection.return_value = mock_collection
            
            mock_doc_ref = MagicMock()
            mock_collection.document.return_value = mock_doc_ref
            
            mock_doc = MagicMock()
            mock_doc.exists = False
            mock_doc_ref.get.return_value = mock_doc
            
            firestore_service = FirestoreService()
            
            job = firestore_service.get_job("nonexistent")
            
            assert job is None
    
    def test_update_job(self):
        """Test updating a job in Firestore."""
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_client = MagicMock()
            mock_firestore.Client.return_value = mock_client
            
            mock_collection = MagicMock()
            mock_client.collection.return_value = mock_collection
            
            mock_doc_ref = MagicMock()
            mock_collection.document.return_value = mock_doc_ref
            
            firestore_service = FirestoreService()
            
            updates = {
                'status': JobStatus.SEPARATING_STAGE1,
                'progress': 25
            }
            
            firestore_service.update_job("test123", updates)
            
            # Verify update was called
            mock_doc_ref.update.assert_called_once()
    
    def test_delete_job(self):
        """Test deleting a job from Firestore."""
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_client = MagicMock()
            mock_firestore.Client.return_value = mock_client
            
            mock_collection = MagicMock()
            mock_client.collection.return_value = mock_collection
            
            mock_doc_ref = MagicMock()
            mock_collection.document.return_value = mock_doc_ref
            
            firestore_service = FirestoreService()
            
            firestore_service.delete_job("test123")
            
            # Verify delete was called
            mock_doc_ref.delete.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

