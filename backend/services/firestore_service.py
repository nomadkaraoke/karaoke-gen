"""
Firestore database operations for job management.
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from backend.config import settings
from backend.models.job import Job, JobStatus, TimelineEvent
from backend.models.worker_log import WorkerLogEntry


logger = logging.getLogger(__name__)


class FirestoreService:
    """Service for Firestore database operations."""
    
    def __init__(self):
        """Initialize Firestore client."""
        self.db = firestore.Client(project=settings.google_cloud_project)
        self.collection = settings.firestore_collection
        self.tokens_collection = "auth_tokens"  # Collection for access tokens
        
    def create_job(self, job: Job) -> None:
        """Create a new job in Firestore."""
        try:
            doc_ref = self.db.collection(self.collection).document(job.job_id)
            doc_ref.set(job.model_dump(mode='json'))
            logger.info(f"Created job {job.job_id} in Firestore")
        except Exception as e:
            logger.error(f"Error creating job {job.job_id}: {e}")
            raise
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        try:
            doc_ref = self.db.collection(self.collection).document(job_id)
            doc = doc_ref.get()
            
            logger.debug(f"Fetching job {job_id} from collection {self.collection}")
            logger.debug(f"Document exists: {doc.exists}")
            
            if not doc.exists:
                return None
            
            data = doc.to_dict()
            logger.debug(f"Document data keys: {list(data.keys()) if data else 'None'}")
            logger.debug(f"Document data: {data}")
            
            return Job(**data)
        except Exception as e:
            logger.error(f"Error getting job {job_id}: {e}")
            raise
    
    def update_job(self, job_id: str, updates: Dict[str, Any]) -> None:
        """Update a job with partial data."""
        try:
            doc_ref = self.db.collection(self.collection).document(job_id)
            
            # Add updated_at timestamp
            updates['updated_at'] = datetime.utcnow()
            
            doc_ref.update(updates)
            logger.info(f"Updated job {job_id} in Firestore")
        except Exception as e:
            logger.error(f"Error updating job {job_id}: {e}")
            raise
    
    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        **additional_fields
    ) -> None:
        """Update job status and add timeline event."""
        try:
            doc_ref = self.db.collection(self.collection).document(job_id)
            
            # Create timeline event
            timeline_event = TimelineEvent(
                status=status.value,
                timestamp=datetime.utcnow().isoformat(),
                progress=progress,
                message=message
            )
            
            # Prepare updates
            updates = {
                'status': status.value,
                'updated_at': datetime.utcnow(),
                'timeline': firestore.ArrayUnion([timeline_event.model_dump(mode='json')])
            }
            
            if progress is not None:
                updates['progress'] = progress
            
            # Add any additional fields
            updates.update(additional_fields)
            
            doc_ref.update(updates)
            logger.info(f"Updated job {job_id} status to {status.value}")
        except Exception as e:
            logger.error(f"Error updating job status {job_id}: {e}")
            raise
    
    def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        environment: Optional[str] = None,
        client_id: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        user_email: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Job]:
        """
        List jobs with optional filters.

        Args:
            status: Filter by job status
            environment: Filter by request_metadata.environment (test/production/development)
            client_id: Filter by request_metadata.client_id
            created_after: Filter jobs created after this datetime
            created_before: Filter jobs created before this datetime
            user_email: Filter by user_email (owner of the job)
            tenant_id: Filter by tenant_id (white-label portal scoping)
            limit: Maximum number of jobs to return

        Returns:
            List of Job objects matching filters, ordered by created_at descending
        """
        try:
            query = self.db.collection(self.collection)

            if status:
                query = query.where(filter=FieldFilter('status', '==', status.value))

            # Filter by request_metadata fields using dot notation
            if environment:
                query = query.where(filter=FieldFilter('request_metadata.environment', '==', environment))

            if client_id:
                query = query.where(filter=FieldFilter('request_metadata.client_id', '==', client_id))

            # Filter by user_email (job owner)
            if user_email:
                query = query.where(filter=FieldFilter('user_email', '==', user_email.lower()))

            # Filter by tenant_id (white-label portal scoping)
            if tenant_id:
                query = query.where(filter=FieldFilter('tenant_id', '==', tenant_id))

            # Date range filters
            if created_after:
                query = query.where(filter=FieldFilter('created_at', '>=', created_after))

            if created_before:
                query = query.where(filter=FieldFilter('created_at', '<=', created_before))

            query = query.order_by('created_at', direction=firestore.Query.DESCENDING).limit(limit)

            docs = query.stream()
            jobs = [Job(**doc.to_dict()) for doc in docs]

            return jobs
        except Exception as e:
            logger.error(f"Error listing jobs: {e}")
            raise
    
    # Fields needed by the dashboard summary view
    SUMMARY_FIELD_PATHS = [
        'job_id', 'status', 'progress', 'created_at', 'artist', 'title',
        'error_message', 'non_interactive', 'outputs_deleted_at', 'user_email',
        'state_data.brand_code', 'state_data.youtube_url', 'state_data.dropbox_link',
        'state_data.audio_progress', 'state_data.lyrics_progress',
        'state_data.audio_complete', 'state_data.lyrics_complete',
        'state_data.backing_vocals_analysis',
        'file_urls.finals', 'file_urls.videos', 'file_urls.packages',
    ]

    def list_jobs_summary(
        self,
        status: Optional[JobStatus] = None,
        exclude_statuses: Optional[List[str]] = None,
        environment: Optional[str] = None,
        client_id: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        user_email: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        List jobs with Firestore field projection for reduced payload.

        Uses ``query.select()`` so Firestore only reads the requested fields,
        drastically cutting bandwidth and deserialization cost.

        Returns raw dicts (not Job models) because projected documents would
        fail Pydantic validation for missing required fields.
        """
        try:
            query = self.db.collection(self.collection)

            if status:
                query = query.where(filter=FieldFilter('status', '==', status.value))

            if exclude_statuses:
                query = query.where(filter=FieldFilter('status', 'not-in', exclude_statuses))

            if environment:
                query = query.where(filter=FieldFilter('request_metadata.environment', '==', environment))

            if client_id:
                query = query.where(filter=FieldFilter('request_metadata.client_id', '==', client_id))

            if user_email:
                query = query.where(filter=FieldFilter('user_email', '==', user_email.lower()))

            if tenant_id:
                query = query.where(filter=FieldFilter('tenant_id', '==', tenant_id))

            if created_after:
                query = query.where(filter=FieldFilter('created_at', '>=', created_after))

            if created_before:
                query = query.where(filter=FieldFilter('created_at', '<=', created_before))

            query = query.order_by('created_at', direction=firestore.Query.DESCENDING).limit(limit)
            query = query.select(self.SUMMARY_FIELD_PATHS)

            docs = query.stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            logger.error(f"Error listing jobs summary: {e}")
            raise

    def delete_jobs_by_filter(
        self,
        environment: Optional[str] = None,
        client_id: Optional[str] = None,
        status: Optional[JobStatus] = None,
        created_before: Optional[datetime] = None,
    ) -> int:
        """
        Delete multiple jobs matching filter criteria.
        
        CAUTION: This is a destructive operation. Use carefully.
        
        Args:
            environment: Delete jobs with this environment (e.g., "test")
            client_id: Delete jobs from this client
            status: Delete jobs with this status
            created_before: Delete jobs created before this datetime
            
        Returns:
            Number of jobs deleted
        """
        try:
            query = self.db.collection(self.collection)
            
            if environment:
                query = query.where(filter=FieldFilter('request_metadata.environment', '==', environment))
            
            if client_id:
                query = query.where(filter=FieldFilter('request_metadata.client_id', '==', client_id))
            
            if status:
                query = query.where(filter=FieldFilter('status', '==', status.value))
            
            if created_before:
                query = query.where(filter=FieldFilter('created_at', '<=', created_before))
            
            # Get matching documents
            docs = list(query.stream())
            deleted_count = 0
            
            # Delete in batches
            batch = self.db.batch()
            batch_count = 0
            
            for doc in docs:
                batch.delete(doc.reference)
                batch_count += 1
                deleted_count += 1
                
                # Firestore batch limit is 500
                if batch_count >= 500:
                    batch.commit()
                    batch = self.db.batch()
                    batch_count = 0
            
            # Commit any remaining deletes
            if batch_count > 0:
                batch.commit()
            
            logger.info(f"Deleted {deleted_count} jobs matching filter criteria")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error deleting jobs by filter: {e}")
            raise
    
    def delete_job(self, job_id: str) -> None:
        """Delete a job from Firestore."""
        try:
            doc_ref = self.db.collection(self.collection).document(job_id)
            doc_ref.delete()
            logger.info(f"Deleted job {job_id} from Firestore")
        except Exception as e:
            logger.error(f"Error deleting job {job_id}: {e}")
            raise
    
    def append_worker_log(self, job_id: str, log_entry: Dict[str, Any]) -> None:
        """
        Atomically append a log entry to worker_logs using ArrayUnion.

        This avoids the race condition of read-modify-write when multiple
        workers are logging concurrently.

        DEPRECATED: Use append_log_to_subcollection() instead to avoid
        the 1MB document size limit.

        Args:
            job_id: Job ID
            log_entry: Log entry dict with timestamp, level, worker, message
        """
        try:
            doc_ref = self.db.collection(self.collection).document(job_id)
            doc_ref.update({
                'worker_logs': firestore.ArrayUnion([log_entry]),
                'updated_at': datetime.utcnow()
            })
            # Don't log every append - too spammy
        except Exception as e:
            # Log but don't raise - logging shouldn't break workers
            logger.debug(f"Error appending worker log for job {job_id}: {e}")

    # ============================================
    # Worker Log Subcollection Methods
    # ============================================
    # These methods store logs in a subcollection (jobs/{job_id}/logs)
    # instead of an embedded array to avoid the 1MB document size limit.

    def append_log_to_subcollection(self, job_id: str, log_entry: WorkerLogEntry) -> None:
        """
        Append a log entry to the logs subcollection.

        Stores logs at: jobs/{job_id}/logs/{log_id}

        This approach avoids the 1MB document size limit by storing each
        log entry as a separate document in a subcollection.

        Args:
            job_id: Job ID
            log_entry: WorkerLogEntry instance
        """
        try:
            # Ensure job_id is set on the log entry
            log_entry.job_id = job_id

            # Get subcollection reference
            logs_ref = self.db.collection(self.collection).document(job_id).collection("logs")

            # Add document with auto-generated ID or use log_entry.id
            doc_ref = logs_ref.document(log_entry.id)
            doc_ref.set(log_entry.to_dict())

            # Don't log every append - too spammy
        except Exception as e:
            # Log but don't raise - logging shouldn't break workers
            logger.debug(f"Error appending log to subcollection for job {job_id}: {e}")

    def get_logs_from_subcollection(
        self,
        job_id: str,
        limit: int = 500,
        since_timestamp: Optional[datetime] = None,
        worker: Optional[str] = None,
        offset: int = 0
    ) -> List[WorkerLogEntry]:
        """
        Get log entries from the logs subcollection.

        Args:
            job_id: Job ID
            limit: Maximum number of logs to return (default 500)
            since_timestamp: Return only logs after this timestamp
            worker: Filter by worker name (optional)
            offset: Number of logs to skip (for pagination)

        Returns:
            List of WorkerLogEntry instances, ordered by timestamp ascending
        """
        try:
            # Get subcollection reference
            logs_ref = self.db.collection(self.collection).document(job_id).collection("logs")

            # Build query
            query = logs_ref.order_by("timestamp", direction=firestore.Query.ASCENDING)

            if since_timestamp:
                query = query.where(filter=FieldFilter("timestamp", ">", since_timestamp))

            if worker:
                query = query.where(filter=FieldFilter("worker", "==", worker))

            # Apply offset and limit
            if offset > 0:
                query = query.offset(offset)

            query = query.limit(limit)

            # Execute query
            docs = query.stream()
            logs = [WorkerLogEntry.from_dict(doc.to_dict()) for doc in docs]

            return logs

        except Exception as e:
            logger.error(f"Error getting logs from subcollection for job {job_id}: {e}")
            return []

    def get_logs_count_from_subcollection(self, job_id: str) -> int:
        """
        Get the total count of log entries in the subcollection.

        Args:
            job_id: Job ID

        Returns:
            Total count of log entries
        """
        try:
            # Get subcollection reference
            logs_ref = self.db.collection(self.collection).document(job_id).collection("logs")

            # Use aggregation query for efficient counting
            count_query = logs_ref.count()
            result = count_query.get()

            # Result is a list of AggregationResult, we want the first one's count
            if result and len(result) > 0:
                return result[0][0].value
            return 0

        except Exception as e:
            logger.error(f"Error counting logs for job {job_id}: {e}")
            return 0

    def delete_logs_subcollection(self, job_id: str, batch_size: int = 500) -> int:
        """
        Delete all log entries in the logs subcollection.

        This is used when deleting a job to clean up its logs.

        Args:
            job_id: Job ID
            batch_size: Number of documents to delete per batch

        Returns:
            Number of logs deleted
        """
        try:
            logs_ref = self.db.collection(self.collection).document(job_id).collection("logs")
            deleted_count = 0

            while True:
                # Get a batch of documents
                docs = logs_ref.limit(batch_size).stream()
                deleted_in_batch = 0

                # Delete in a batch
                batch = self.db.batch()
                for doc in docs:
                    batch.delete(doc.reference)
                    deleted_in_batch += 1

                if deleted_in_batch == 0:
                    break

                batch.commit()
                deleted_count += deleted_in_batch

                # If we deleted less than batch_size, we're done
                if deleted_in_batch < batch_size:
                    break

            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} logs for job {job_id}")

            return deleted_count

        except Exception as e:
            logger.error(f"Error deleting logs subcollection for job {job_id}: {e}")
            return 0
    
    # ============================================
    # Token Management Methods
    # ============================================
    
    def create_token(self, token: str, token_data: Dict[str, Any]) -> None:
        """Create a new access token in Firestore."""
        try:
            doc_ref = self.db.collection(self.tokens_collection).document(token)
            doc_ref.set(token_data)
            logger.info(f"Created token in Firestore")
        except Exception as e:
            logger.error(f"Error creating token: {e}")
            raise
    
    def get_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Get token data by token string."""
        try:
            doc_ref = self.db.collection(self.tokens_collection).document(token)
            doc = doc_ref.get()
            
            if not doc.exists:
                return None
            
            return doc.to_dict()
        except Exception as e:
            logger.error(f"Error getting token: {e}")
            return None
    
    def update_token(self, token: str, updates: Dict[str, Any]) -> None:
        """Update token data."""
        try:
            doc_ref = self.db.collection(self.tokens_collection).document(token)
            doc_ref.update(updates)
            logger.info(f"Updated token in Firestore")
        except Exception as e:
            logger.error(f"Error updating token: {e}")
            raise
    
    def increment_token_usage(self, token: str, job_id: str) -> None:
        """Increment token usage count and add job to history."""
        try:
            doc_ref = self.db.collection(self.tokens_collection).document(token)
            
            # Use Firestore transaction to ensure atomic increment
            @firestore.transactional
            def update_in_transaction(transaction, doc_ref):
                snapshot = doc_ref.get(transaction=transaction)
                if not snapshot.exists:
                    raise ValueError("Token not found")
                
                data = snapshot.to_dict()
                current_usage = data.get("usage_count", 0)
                jobs = data.get("jobs", [])
                
                # Increment usage
                transaction.update(doc_ref, {
                    "usage_count": current_usage + 1,
                    "last_used": datetime.utcnow(),
                    "jobs": firestore.ArrayUnion([{
                        "job_id": job_id,
                        "created_at": datetime.utcnow()
                    }])
                })
            
            transaction = self.db.transaction()
            update_in_transaction(transaction, doc_ref)
            
            logger.info(f"Incremented token usage for job {job_id}")
        except Exception as e:
            logger.error(f"Error incrementing token usage: {e}")
            raise
    
    def list_tokens(self) -> List[Dict[str, Any]]:
        """List all tokens (admin only)."""
        try:
            docs = self.db.collection(self.tokens_collection).stream()
            tokens = [doc.to_dict() for doc in docs]
            logger.info(f"Retrieved {len(tokens)} tokens from Firestore")
            return tokens
        except Exception as e:
            logger.error(f"Error listing tokens: {e}")
            return []


# Singleton client instance
_firestore_client: Optional[firestore.Client] = None


def get_firestore_client() -> firestore.Client:
    """
    Get a shared Firestore client instance.

    This returns a raw Firestore client (not the FirestoreService) for use
    in services that need direct Firestore access without the job-specific
    abstractions provided by FirestoreService.

    Returns:
        Firestore client instance
    """
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(project=settings.google_cloud_project)
    return _firestore_client

