"""
Firestore database resources.

Manages the Firestore database, composite indexes, and TTL configuration.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import firestore

from config import PROJECT_ID


def create_database() -> dict:
    """
    Create Firestore database and all composite indexes.

    Returns:
        dict: Dictionary containing all created resources for export.
    """
    resources = {}

    # ==================== Firestore Database ====================
    firestore_db = firestore.Database(
        "karaoke-firestore",
        project=PROJECT_ID,
        name="(default)",
        location_id="us-central1",
        type="FIRESTORE_NATIVE",
        concurrency_mode="PESSIMISTIC",
        app_engine_integration_mode="DISABLED",
    )
    resources["firestore_db"] = firestore_db

    # ==================== Firestore Composite Indexes ====================
    # These are required for queries that filter/sort on multiple fields.

    # Jobs: query by user_email, order by created_at (for user job lists)
    resources["firestore_index_jobs_user_email"] = firestore.Index(
        "firestore-index-jobs-user-email",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="jobs",
        fields=[
            firestore.IndexFieldArgs(field_path="user_email", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Jobs: query by status, order by created_at (for admin/system job lists)
    resources["firestore_index_jobs_status"] = firestore.Index(
        "firestore-index-jobs-status",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="jobs",
        fields=[
            firestore.IndexFieldArgs(field_path="status", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Jobs: query by tenant_id, order by created_at (for tenant portal job lists)
    resources["firestore_index_jobs_tenant"] = firestore.Index(
        "firestore-index-jobs-tenant",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="jobs",
        fields=[
            firestore.IndexFieldArgs(field_path="tenant_id", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(
            depends_on=[firestore_db],
            import_="projects/nomadkaraoke/databases/(default)/collectionGroups/jobs/indexes/CICAgJjUo5EK",
        ),
    )

    # Jobs: query by user_email + tenant_id, order by created_at (for tenant user job lists)
    resources["firestore_index_jobs_user_tenant"] = firestore.Index(
        "firestore-index-jobs-user-tenant",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="jobs",
        fields=[
            firestore.IndexFieldArgs(field_path="user_email", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="tenant_id", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(
            depends_on=[firestore_db],
            import_="projects/nomadkaraoke/databases/(default)/collectionGroups/jobs/indexes/CICAgJjFx5sK",
        ),
    )

    # Jobs: query by customer_email + made_for_you, order by created_at
    # Used for finding existing made-for-you orders for a customer
    resources["firestore_index_jobs_customer_mfy"] = firestore.Index(
        "firestore-index-jobs-customer-mfy",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="jobs",
        fields=[
            firestore.IndexFieldArgs(field_path="customer_email", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="made_for_you", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(
            depends_on=[firestore_db],
            # Import existing index created before Pulumi managed it
            import_="projects/nomadkaraoke/databases/(default)/collectionGroups/jobs/indexes/CICAgOi36pgK",
        ),
    )

    # Sessions: query by user_email + is_active, order by created_at
    resources["firestore_index_sessions_active"] = firestore.Index(
        "firestore-index-sessions-active",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="sessions",
        fields=[
            firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="user_email", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Magic links: query by email, order by created_at
    resources["firestore_index_magic_links"] = firestore.Index(
        "firestore-index-magic-links",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="magic_links",
        fields=[
            firestore.IndexFieldArgs(field_path="email", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Gen Users: query by is_active, order by created_at
    resources["firestore_index_gen_users_active_created"] = firestore.Index(
        "firestore-index-gen-users-active-created",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="gen_users",
        fields=[
            firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Gen Users: query by is_active, order by last_login_at
    resources["firestore_index_gen_users_active_login"] = firestore.Index(
        "firestore-index-gen-users-active-login",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="gen_users",
        fields=[
            firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="last_login_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Gen Users: query by is_active, order by credits
    resources["firestore_index_gen_users_active_credits"] = firestore.Index(
        "firestore-index-gen-users-active-credits",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="gen_users",
        fields=[
            firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="credits", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Gen Users: query by is_active, order by email
    resources["firestore_index_gen_users_active_email"] = firestore.Index(
        "firestore-index-gen-users-active-email",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="gen_users",
        fields=[
            firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="email", order="ASCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Gen Users: query by signup_ip, order by created_at (anti-abuse investigation)
    resources["firestore_index_gen_users_signup_ip"] = firestore.Index(
        "firestore-index-gen-users-signup-ip",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="gen_users",
        fields=[
            firestore.IndexFieldArgs(field_path="signup_ip", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Gen Users: query by device_fingerprint, order by created_at (anti-abuse investigation)
    resources["firestore_index_gen_users_device_fingerprint"] = firestore.Index(
        "firestore-index-gen-users-device-fingerprint",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="gen_users",
        fields=[
            firestore.IndexFieldArgs(field_path="device_fingerprint", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Stripe Payments: query by customer_email, order by created_at
    # Used for admin user detail page payment history
    resources["firestore_index_stripe_payments_email"] = firestore.Index(
        "firestore-index-stripe-payments-email",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="stripe_payments",
        fields=[
            firestore.IndexFieldArgs(field_path="customer_email", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # ==================== Firestore TTL Field Configuration ====================
    # TTL policies automatically delete documents after expiration.

    # Worker logs TTL: logs subcollection documents expire after 30 days
    resources["firestore_field_logs_ttl"] = firestore.Field(
        "firestore-field-logs-ttl",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="logs",  # Subcollection name (applies to all jobs/{job_id}/logs)
        field="ttl_expiry",
        ttl_config={},  # Empty block enables TTL based on ttl_expiry field
        index_config={},  # Disable indexing on TTL field
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # Search sessions TTL: short-lived sessions from guided job creation flow expire after 30 minutes
    # Sessions are created by POST /api/audio-search/search-standalone and consumed by
    # POST /api/jobs/create-from-search.  The TTL is a safety net for abandoned sessions.
    resources["firestore_field_search_sessions_ttl"] = firestore.Field(
        "firestore-field-search-sessions-ttl",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="search_sessions",
        field="ttl_expiry",
        ttl_config={},  # Empty block enables TTL based on ttl_expiry field
        index_config={},  # Disable indexing on TTL field
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    # ==================== Review Sessions Collection Group Index ====================

    # The cross-job session search uses a collection group query on
    # review_sessions ordered by updated_at DESC.  Firestore requires a
    # single-field index exemption with COLLECTION_GROUP scope for this.
    # Created via REST API (gcloud doesn't support query-scope for field exemptions):
    #
    #   curl -X PATCH \
    #     "https://firestore.googleapis.com/v1/projects/nomadkaraoke/databases/(default)/collectionGroups/review_sessions/fields/updated_at" \
    #     -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    #     -H "Content-Type: application/json" \
    #     -d '{"indexConfig":{"indexes":[
    #       {"queryScope":"COLLECTION","fields":[{"fieldPath":"updated_at","order":"ASCENDING"}]},
    #       {"queryScope":"COLLECTION","fields":[{"fieldPath":"updated_at","order":"DESCENDING"}]},
    #       {"queryScope":"COLLECTION_GROUP","fields":[{"fieldPath":"updated_at","order":"DESCENDING"}]}
    #     ]}}'
    #
    # Pulumi doesn't have a native resource for single-field index
    # exemptions — manage via gcloud/REST if it needs recreation.

    # ==================== Firestore Indexes for Logs Subcollection ====================

    # Logs: query by worker, order by timestamp
    resources["firestore_index_logs_worker_timestamp"] = firestore.Index(
        "firestore-index-logs-worker-timestamp",
        project=PROJECT_ID,
        database=firestore_db.name,
        collection="logs",  # Subcollection name
        fields=[
            firestore.IndexFieldArgs(field_path="worker", order="ASCENDING"),
            firestore.IndexFieldArgs(field_path="timestamp", order="ASCENDING"),
        ],
        opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
    )

    return resources
