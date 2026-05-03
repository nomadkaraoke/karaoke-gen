"""Regression tests for the StructuredFormatter — specifically that arbitrary
fields passed via ``extra=`` reach the JSON payload (not silently dropped)."""
from __future__ import annotations

import json
import logging

from backend.services.structured_logging import StructuredFormatter


def _format(record_extra: dict, message: str = "test") -> dict:
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )
    for k, v in record_extra.items():
        setattr(record, k, v)
    return json.loads(formatter.format(record))


def test_arbitrary_extra_fields_are_forwarded() -> None:
    """A future feature owner adds `extra={"my_new_field": 42}` and expects
    to see it in Cloud Logging. The formatter must NOT use an allowlist."""
    payload = _format({"my_new_field": 42, "another_one": "hello"})
    assert payload["my_new_field"] == 42
    assert payload["another_one"] == "hello"


def test_complex_extra_values_are_forwarded() -> None:
    """Diagnostic fields are often dicts/lists (e.g. worst_lines). Verify."""
    payload = _format({
        "worst_lines": [{"line_index": 3, "min_delta": 5}],
        "settings": {"strictness": "balanced", "allow_reword": True},
    })
    assert payload["worst_lines"] == [{"line_index": 3, "min_delta": 5}]
    assert payload["settings"]["strictness"] == "balanced"


def test_none_values_are_omitted() -> None:
    payload = _format({"defined": "yes", "absent": None})
    assert payload["defined"] == "yes"
    assert "absent" not in payload


def test_underscore_prefixed_attrs_skipped() -> None:
    """LogRecord internals like _internal shouldn't leak."""
    payload = _format({"_private": "secret", "public": "ok"})
    assert "_private" not in payload
    assert payload["public"] == "ok"


def test_standard_record_attrs_not_duplicated() -> None:
    """Don't echo `name`, `levelname`, etc. as custom fields."""
    payload = _format({})
    # These are added explicitly by the formatter, not as forwarded extras
    assert payload["logger"] == "test"
    assert payload["severity"] == "INFO"
    assert payload["message"] == "test"
    # `args`, `levelname`, `pathname` etc. should not appear as top-level keys
    for reserved in ("args", "levelname", "lineno", "pathname", "msg"):
        assert reserved not in payload, f"reserved attr {reserved} leaked"
