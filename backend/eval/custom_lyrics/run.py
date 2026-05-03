"""CLI: python -m backend.eval.custom_lyrics.run"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.eval.custom_lyrics.cache import LlmCallCache
from backend.eval.custom_lyrics.report import write_baseline, write_run_reports
from backend.eval.custom_lyrics.runner import run_fixture
from backend.services.custom_lyrics.service import CustomLyricsService

EVAL_ROOT = Path(__file__).parent
FIXTURES_DIR = EVAL_ROOT / "fixtures"
RESULTS_DIR = EVAL_ROOT / "results"
CACHE_DIR = EVAL_ROOT / "cache"
BASELINE_PATH = EVAL_ROOT / "baseline.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", default="all", help="fixture id, or comma-separated, or 'all'")
    parser.add_argument("--settings", default=None, help="settings name from metadata.settings_to_test")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--save-as-baseline", default=None,
                        help="path to write baseline JSON (e.g., baseline-post.json)")
    args = parser.parse_args()

    cache = LlmCallCache(
        CACHE_DIR,
        replay_only=args.replay_only,
    ) if not args.no_cache else _NullCache()

    fixture_dirs = _select_fixtures(args.fixtures)
    service = CustomLyricsService()

    results = []
    for fdir in fixture_dirs:
        meta = json.loads((fdir / "metadata.json").read_text())
        candidate_settings = meta.get("settings_to_test", [{
            "name": "default",
            "allow_reword": True,
            "allow_omit": True,
            "fixed_line_count": True,
            "strictness": "balanced",
        }])
        for s in candidate_settings:
            if args.settings and s["name"] != args.settings:
                continue
            settings_dict = {k: v for k, v in s.items() if k != "name"}
            print(f"Running {fdir.name} / {s['name']}...", file=sys.stderr)
            results.append(run_fixture(
                fdir, s["name"], settings_dict,
                cache=cache, service=service,
            ))

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    out_dir = RESULTS_DIR / ts
    baseline = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else None
    write_run_reports(results, out_dir, baseline=baseline)

    if args.save_as_baseline:
        write_baseline(results, EVAL_ROOT / args.save_as_baseline)
        print(f"Baseline saved to {args.save_as_baseline}", file=sys.stderr)

    print(f"\nReport: {out_dir / 'summary.md'}", file=sys.stderr)
    return 0


def _select_fixtures(spec: str) -> list[Path]:
    if spec == "all":
        return sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir() and (p / "metadata.json").exists())
    ids = [s.strip() for s in spec.split(",")]
    return [FIXTURES_DIR / i for i in ids]


class _NullCache:
    def get(self, *args, **kwargs): return None
    def set(self, *args, **kwargs): pass


if __name__ == "__main__":
    sys.exit(main())
