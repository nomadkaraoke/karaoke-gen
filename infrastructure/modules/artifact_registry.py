"""
Artifact Registry resources.

Manages the Docker repository for backend container images.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import artifactregistry

from config import PROJECT_ID, REGION


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
        # Cost control: keep the 10 most recent versions for rollback, and
        # delete untagged digests older than 7 days (orphaned layers left by
        # repeated :latest pushes). Tagged images are never auto-deleted.
        cleanup_policies=[
            artifactregistry.RepositoryCleanupPolicyArgs(
                id="keep-recent-10",
                action="KEEP",
                most_recent_versions=artifactregistry.RepositoryCleanupPolicyMostRecentVersionsArgs(
                    keep_count=10,
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
        ],
        cleanup_policy_dry_run=False,
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
