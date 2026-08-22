"""Artist/title match-judge: deterministic + catalog + light-AI formatting and
match-acceptance decisions for the job-submission flow.

See docs/archive/2026-06-15-artist-title-matching-rework-plan.md.
"""
from backend.services.match_judge.verdict import MatchVerdict

__all__ = ["MatchVerdict"]
