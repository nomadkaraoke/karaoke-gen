"""Scripted match-judge / free-text-resolve verdicts for the local E2E stack (never calls Gemini).

``POST /api/catalog/match-judge`` uses the signed-in user's fake config (``email``);
``POST /api/kjbox/catalog/resolve`` is called by partners with no user, so it reads the
GLOBAL config. Knobs: ``judge_kind`` / ``judge_canonical_artist`` / ``judge_canonical_title``
(see config.py and docs/TESTING.md § Local stack).
"""
from __future__ import annotations

from typing import Optional

from backend.services.fake_workers.config import FakeConfig, get_fake_config
from backend.services.match_judge.verdict import KIND_AMBIGUOUS, KIND_NONE, MatchVerdict

DEFAULT_ARTIST = "Fake Artist"
DEFAULT_TITLE = "Fake Title"


def _canonical(cfg: FakeConfig) -> tuple:
    return cfg.judge_canonical_artist or DEFAULT_ARTIST, cfg.judge_canonical_title or DEFAULT_TITLE


def verdict_for(cfg: FakeConfig, artist: str, title: str) -> MatchVerdict:
    kind = cfg.judge_kind
    if kind == KIND_NONE:
        return MatchVerdict(KIND_NONE, False, artist, title, engine="ai", reason="fake: none")
    ca, ct = _canonical(cfg)
    alternatives = []
    if kind == KIND_AMBIGUOUS:
        alternatives = [{"artist": ca, "title": ct}, {"artist": ca, "title": f"{ct} (Live)"}]
    return MatchVerdict(
        kind, kind != KIND_AMBIGUOUS, ca, ct, alternatives=alternatives,
        engine="ai", reason=f"fake: {kind}",
    )


def fake_match_verdict(artist: str, title: str, email: Optional[str] = None) -> MatchVerdict:
    """Fake ``judge_match`` verdict from ``email``'s (else global) fake config."""
    return verdict_for(get_fake_config(email=email), artist, title)


def fake_resolve(query: str) -> dict:
    """Fake ``resolve_and_tidy`` response (verdict + typed_artist/typed_title) from the GLOBAL config."""
    v = verdict_for(get_fake_config(), "", "").to_dict()
    v.pop("needs_ai")   # not part of the resolve response shape
    return {**v, "typed_artist": "", "typed_title": query}
