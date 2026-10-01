"""
Artifact Registry resources.

Manages the Docker repository for backend container images.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import artifactregistry

from config import PROJECT_ID, REGION

# Flip to True to make AR only *log* what the cleanup policies would delete
# (Cloud Audit Logs, "dry run" DeleteVersions) instead of deleting.
CLEANUP_POLICY_DRY_RUN = True

# Tags that must never be auto-deleted regardless of age:
#   latest   — what jobs/services pinned to :latest pull
#   content- — the GPU base image child manifest (content-<hash>), deliberately
#              tagged so the untagged-delete rule can't orphan it
#   cache    — BuildKit registry cache manifests (cache-from/cache-to)
PROTECTED_TAG_PREFIXES = ["latest", "content-", "cache"]


def standard_cleanup_policies() -> list:
    """Shared cleanup policy set for the Docker repos (GCP cost cut 2026-10-01).

    KEEP rules always win over DELETE rules in Artifact Registry, so a version
    survives if it is among the 10 most recent versions of its package OR
    carries a protected tag. Everything else is deleted once older than 7 days
    (untagged digests AND old per-commit ``<sha>``/``v<version>`` tags — before
    this, tagged images were never deleted and every CI deploy accumulated).
    The currently deployed image of every Cloud Run service/job is always among
    the 10 most recent (or is pinned to :latest), so rollback to recent
    revisions keeps working.
    """
    return [
        artifactregistry.RepositoryCleanupPolicyArgs(
            id="keep-recent-10",
            action="KEEP",
            most_recent_versions=artifactregistry.RepositoryCleanupPolicyMostRecentVersionsArgs(
                keep_count=10,
            ),
        ),
        artifactregistry.RepositoryCleanupPolicyArgs(
            id="keep-protected-tags",
            action="KEEP",
            condition=artifactregistry.RepositoryCleanupPolicyConditionArgs(
                tag_state="TAGGED",
                tag_prefixes=PROTECTED_TAG_PREFIXES,
            ),
        ),
        artifactregistry.RepositoryCleanupPolicyArgs(
            id="delete-untagged-after-7d",
            action="DELETE",
            condition=artifactregistry.RepositoryCleanupPolicyConditionArgs(
                tag_state="UNTAGGED",
                older_than="604800s",  # 7 days
            ),
        ),
        artifactregistry.RepositoryCleanupPolicyArgs(
            id="delete-tagged-after-7d",
            action="DELETE",
            condition=artifactregistry.RepositoryCleanupPolicyConditionArgs(
                tag_state="TAGGED",
                older_than="604800s",  # 7 days
            ),
        ),
    ]


def create_repository() -> artifactregistry.Repository:
    """
    Create the Artifact Registry repository for Docker images.

    Returns:
        artifactregistry.Repository: The created repository resource.
    """
    artifact_repo = artifactregistry.Repository(
        "karaoke-artifact-repo",
        repository_id="karaoke-repo",
        location=REGION,
        format="DOCKER",
        description="Docker repository for karaoke backend images",
        cleanup_policies=standard_cleanup_policies(),
        cleanup_policy_dry_run=CLEANUP_POLICY_DRY_RUN,
    )

    return artifact_repo


def get_repo_url(repo: artifactregistry.Repository) -> pulumi.Output[str]:
    """
    Get the full URL for the repository.

    Args:
        repo: The artifact registry repository.

    Returns:
        pulumi.Output[str]: The repository URL (e.g., us-central1-docker.pkg.dev/project/repo)
    """
    return repo.name.apply(lambda name: f"{REGION}-docker.pkg.dev/{PROJECT_ID}/karaoke-repo")
