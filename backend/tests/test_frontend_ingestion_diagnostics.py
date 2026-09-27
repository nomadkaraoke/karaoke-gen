"""Frontend crash-report samples carry client diagnostics ahead of the stack.

Regression: a Firefox "out of memory" alert (2026-09-26) had no real stack — the
reporter synthesized an Error whose stack was its own handler — and the sample
put that stack first, so Discord truncated away URL/UA/build. Samples now lead
with context + diagnostics + breadcrumb trail, trim the stack, and stackless
errors group by message + location instead of the reporter's stack.
"""
from backend.services.error_monitor.frontend_ingestion import (
    MAX_SAMPLE_STACK_LINES,
    FrontendErrorReport,
    build_pattern_data,
)


def _report(**overrides):
    base = dict(
        message="Error: out of memory",
        stack=None,
        url="https://gen.nomadkaraoke.com/en/app/?token=secret",
        user_agent="Mozilla/5.0 Firefox/156.0",
        release="abc123",
        user_email=None,
        viewport=None,
        locale="en",
        extra={
            "filename": "https://gen.nomadkaraoke.com/_next/static/chunks/94bde.js",
            "lineno": 1,
            "colno": 208587,
            "synthetic_error": True,
            "diagnostics": {"page_age_s": 11520, "dom_nodes": 5400, "live_blob_urls": 2, "js_heap_used_mb": 1900},
            "breadcrumbs": [
                {"t": 11400.2, "category": "click", "message": 'button "Create Karaoke Video"'},
                {"t": 11401.0, "category": "upload", "message": "uploading file 2/2 (88 MB)"},
            ],
        },
    )
    base.update(overrides)
    return FrontendErrorReport(**base)


def test_stackless_sample_leads_with_location_context_diagnostics_and_trail():
    sample = build_pattern_data(_report()).sample_message
    lines = sample.splitlines()
    assert lines[0] == "Error: out of memory"
    assert lines[1].startswith("At: https://gen.nomadkaraoke.com/_next/static/chunks/94bde.js:1:208587")
    assert "URL: https://gen.nomadkaraoke.com/en/app/" in sample and "secret" not in sample
    assert "UA: Mozilla/5.0 Firefox/156.0" in sample
    assert "Build: abc123" in sample
    assert "Diag: page_age_s=11520 dom_nodes=5400 live_blob_urls=2 js_heap_used_mb=1900" in sample
    assert '[upload] uploading file 2/2 (88 MB)' in sample
    assert "Stack:" not in sample


def test_stackless_errors_group_by_message_not_reporter_stack():
    """One pattern per message across builds/chunks — not a fresh alert per deploy."""
    a = build_pattern_data(_report())
    b = build_pattern_data(_report(extra={**_report().extra, "breadcrumbs": []}))
    new_build = build_pattern_data(_report(extra={**_report().extra, "filename": "https://gen.nomadkaraoke.com/_next/static/chunks/7a01e9c66517b3c3.js"}))
    other_msg = build_pattern_data(_report(message="Error: too much recursion"))
    assert a.pattern_id == b.pattern_id == new_build.pattern_id
    assert a.pattern_id != other_msg.pattern_id


def test_stack_goes_last_and_is_trimmed():
    frames = "\n".join(f"    at frame{i} (app.js:{i}:1)" for i in range(40))
    sample = build_pattern_data(_report(message="TypeError: x is undefined", stack=frames)).sample_message
    ctx_idx = sample.index("Build: abc123")
    stack_idx = sample.index("Stack:")
    assert ctx_idx < stack_idx
    assert f"frame{MAX_SAMPLE_STACK_LINES - 1} " in sample
    assert f"frame{MAX_SAMPLE_STACK_LINES} " not in sample
    assert f"{40 - MAX_SAMPLE_STACK_LINES} more frames" in sample


def test_reports_without_extra_still_work():
    sample = build_pattern_data(_report(extra=None, stack="Error: boom\n  at a (x.js:1:1)")).sample_message
    assert sample.startswith("Error: out of memory")
    assert "Diag:" not in sample and "Trail:" not in sample
