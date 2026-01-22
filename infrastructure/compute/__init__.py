"""
Compute resources module.

Contains VM definitions for:
- Encoding worker (video encoding)
- GitHub self-hosted runners
"""

from .encoding_worker_vm import (
    create_encoding_worker_vm,
    create_encoding_worker_ip,
    create_encoding_worker_firewall,
)
from .github_runners import create_github_runners

__all__ = [
    "create_encoding_worker_vm",
    "create_encoding_worker_ip",
    "create_encoding_worker_firewall",
    "create_github_runners",
]
