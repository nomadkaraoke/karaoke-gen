"""Markdown report generation for eval runs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.eval.custom_lyrics.runner import FixtureRunResult
from backend.eval.custom_lyrics.scorer import aggregate_corpus


def write_run_reports(
    results: list[FixtureRunResult],
    out_dir: Path,
    baseline: Optional[dict] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "per_fixture").mkdir(exist_ok=True)

    summary_lines = ["# Custom Lyrics Eval Run", ""]
    summary_lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    summary_lines.append("")
    summary_lines.append("## Per-fixture summary")
    summary_lines.append("")
    summary_lines.append("| Fixture | Settings | pass@±2 | Δ | mean_Δ | iters | stop |")
    summary_lines.append("|---------|----------|---------|---|--------|-------|------|")
    for r in results:
        s = r.score
        delta_str = ""
        if baseline:
            prev = baseline.get(r.fixture_id, {}).get(r.settings_name, {})
            if "pct_pass_at_2" in prev:
                d = s.pct_pass_at_2 - prev["pct_pass_at_2"]
                delta_str = f"{d:+.2f}"
        summary_lines.append(
            f"| {r.fixture_id} | {r.settings_name} | {s.pct_pass_at_2:.2f} | "
            f"{delta_str} | {s.mean_delta:.2f} | {s.iterations_used} | {s.stop_reason} |"
        )

    summary_lines.append("")
    corpus = aggregate_corpus([r.score for r in results])
    summary_lines.append("## Corpus aggregate")
    summary_lines.append("")
    summary_lines.append(f"- Macro pass@±2: {corpus.get('macro_pct_pass_at_2', 0):.2f}")
    summary_lines.append(f"- Macro pass@±1: {corpus.get('macro_pct_pass_at_1', 0):.2f}")
    summary_lines.append(f"- Macro pass@±0: {corpus.get('macro_pct_pass_at_0', 0):.2f}")
    summary_lines.append(f"- Total Gemini calls: {corpus.get('total_gemini_calls', 0)}")

    (out_dir / "summary.md").write_text("\n".join(summary_lines))

    for r in results:
        per_dir = out_dir / "per_fixture" / f"{r.fixture_id}__{r.settings_name}"
        per_dir.mkdir(parents=True, exist_ok=True)
        (per_dir / "output.json").write_text(json.dumps({
            "fixture_id": r.fixture_id,
            "settings_name": r.settings_name,
            "score": r.score.__dict__,
            "lines": r.output_lines,
            "line_metadata": r.line_metadata,
        }, indent=2))
        _write_per_fixture_md(per_dir, r)


def _write_per_fixture_md(out_dir: Path, r: FixtureRunResult) -> None:
    lines = [
        f"# {r.fixture_id} — {r.settings_name}",
        "",
        f"Stop reason: **{r.score.stop_reason}** after {r.score.iterations_used} iterations.",
        f"Pass@±2: **{r.score.pct_pass_at_2:.0%}** ({sum(1 for v in r.line_metadata if v['min_delta']<=2)}/{r.score.line_count})",
        "",
        "| # | Original target | Candidate | Δ | Severity |",
        "|---|------------------|-----------|---|----------|",
    ]
    for v in r.line_metadata:
        lines.append(
            f"| {v['line_index']+1} | {_escape(v['target_text'])} | "
            f"{_escape(v['candidate_text'])} | {v['min_delta']} | {v['severity']} |"
        )
    lines += [
        "",
        "## Your rating",
        "",
        "Add your qualitative notes here:",
        "- [ ] Singable end-to-end",
        "- [ ] Names placed naturally",
        "- [ ] Stress patterns reasonable",
        "- Notes:",
        "",
    ]
    (out_dir / "output.md").write_text("\n".join(lines))


def _escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def write_baseline(results: list[FixtureRunResult], path: Path) -> None:
    out: dict = {}
    for r in results:
        bucket = out.setdefault(r.fixture_id, {})
        bucket[r.settings_name] = {
            "pct_pass_at_2": r.score.pct_pass_at_2,
            "pct_pass_at_0": r.score.pct_pass_at_0,
            "mean_delta": r.score.mean_delta,
            "iterations_used": r.score.iterations_used,
            "captured_at": datetime.now(timezone.utc).date().isoformat(),
        }
    path.write_text(json.dumps(out, indent=2))
