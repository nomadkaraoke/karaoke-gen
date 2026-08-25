"""Tenant bulk-upload helpers.

Filename analysis for the tenant portal "bulk upload" flow: pairs Mixed vs
Instrumental audio files and extracts Artist/Title from filenames so an operator
can review/fix an editable table before submitting many karaoke jobs at once.
"""

from backend.services.tenant_bulk.analyze import (
    BulkAnalysis,
    IgnoredFile,
    ProposedRow,
    UnpairedFile,
    analyze_filenames,
)

__all__ = [
    "BulkAnalysis",
    "IgnoredFile",
    "ProposedRow",
    "UnpairedFile",
    "analyze_filenames",
]
