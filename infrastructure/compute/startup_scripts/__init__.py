"""
Startup scripts for VM instances.

Provides helper functions to load startup scripts from external files,
making them easier to edit, test, and maintain.
"""

from pathlib import Path


def read_script(name: str, **substitutions) -> str:
    """
    Read a startup script file and apply substitutions.

    Args:
        name: Name of the script file (e.g., "flacfetch.sh")
        **substitutions: Key-value pairs for template substitution.
            Keys should match ${KEY} patterns in the script.

    Returns:
        str: The script content with substitutions applied.

    Example:
        script = read_script("github_runner.sh",
            RUNNER_VERSION="2.332.0",
            RUNNER_LABELS="self-hosted,linux,x64"
        )
    """
    script_path = Path(__file__).parent / name
    content = script_path.read_text()

    for key, value in substitutions.items():
        content = content.replace(f"${{{key}}}", str(value))

    return content


__all__ = ["read_script"]
