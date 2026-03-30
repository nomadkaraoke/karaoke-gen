"""
Claude read-only service account and break-glass SA for business continuity.

The claude-readonly SA provides safe default credentials for Claude Code sessions.
The break-glass SA is excepted from deny policies for emergency operations.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import serviceaccount

from config import PROJECT_ID


def create_claude_readonly_service_account() -> serviceaccount.Account:
    """Create read-only service account for Claude Code sessions."""
    return serviceaccount.Account(
        "claude-readonly-sa",
        account_id="claude-readonly",
        display_name="Claude Code Read-Only",
        description="Default read-only SA for Claude Code sessions — no write access to production",
    )


def create_break_glass_service_account() -> serviceaccount.Account:
    """Create break-glass SA excepted from IAM deny policies."""
    return serviceaccount.Account(
        "break-glass-sa",
        account_id="break-glass",
        display_name="Break Glass Emergency Access",
        description="Emergency SA excepted from deny policies — key stored offline only",
    )


def grant_claude_readonly_permissions(
    service_account: serviceaccount.Account,
) -> dict:
    """Grant read-only permissions across the project."""
    bindings = {}
    roles = {
        "viewer": "roles/viewer",
        "firestore_viewer": "roles/datastore.viewer",
        "bigquery_data_viewer": "roles/bigquery.dataViewer",
        "bigquery_job_user": "roles/bigquery.jobUser",
        "storage_viewer": "roles/storage.objectViewer",
        "secretmanager_viewer": "roles/secretmanager.viewer",
        "run_viewer": "roles/run.viewer",
        "compute_viewer": "roles/compute.viewer",
        "logging_viewer": "roles/logging.viewer",
    }

    for key, role in roles.items():
        bindings[key] = gcp.projects.IAMMember(
            f"claude-readonly-{key.replace('_', '-')}",
            project=PROJECT_ID,
            role=role,
            member=service_account.email.apply(
                lambda email: f"serviceAccount:{email}"
            ),
        )

    return bindings


def grant_impersonation_permission(
    service_account: serviceaccount.Account,
) -> serviceaccount.IAMBinding:
    """Allow users to impersonate the read-only SA."""
    return serviceaccount.IAMBinding(
        "claude-readonly-impersonation",
        service_account_id=service_account.name,
        role="roles/iam.serviceAccountTokenCreator",
        members=[
            "user:andrew@beveridge.uk",
            "user:admin@nomadkaraoke.com",
        ],
    )


def create_deny_policies(
    break_glass_sa: serviceaccount.Account,
) -> dict:
    """Create IAM deny policies to prevent catastrophic deletes.

    The break-glass SA is excepted from all deny policies.
    Uses project NUMBER (not ID) for principal URIs as required by IAM v2.
    """
    from config import get_project_number

    policies = {}
    project_number = get_project_number()

    # Deny destructive operations project-wide
    policies["deny_destructive_ops"] = gcp.iam.DenyPolicy(
        "deny-destructive-operations",
        parent=f"cloudresourcemanager.googleapis.com/projects/{project_number}",
        name="deny-destructive-operations",
        rules=[
            gcp.iam.DenyPolicyRuleArgs(
                deny_rule=gcp.iam.DenyPolicyRuleDenyRuleArgs(
                    denied_principals=["principalSet://goog/public:all"],
                    denied_permissions=[
                        "firestore.googleapis.com/databases.delete",
                        "storage.googleapis.com/buckets.delete",
                        "bigquery.googleapis.com/datasets.delete",
                    ],
                    exception_principals=[
                        break_glass_sa.email.apply(
                            lambda email: f"principal://iam.googleapis.com/projects/{project_number}/serviceAccounts/{email}"
                        ),
                    ],
                ),
            ),
        ],
    )

    return policies
