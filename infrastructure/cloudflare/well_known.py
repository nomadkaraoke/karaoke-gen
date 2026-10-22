"""kjbox.cc/.well-known/* Worker: script builder + the redirect-rule exclusion (no pulumi imports, unit-testable)."""
import json
import pathlib

WORKER_JS = pathlib.Path(__file__).parent / "kjbox-cc-well-known" / "worker.js"
PLACEHOLDER = "/*FINGERPRINTS*/[]/*END*/"
ROUTE_PATTERN = "kjbox.cc/.well-known/*"

# Appended to the kjbox.cc/<code> redirect rule so the Worker route (not the redirect) answers.
WELL_KNOWN_EXCLUSION = ' and not starts_with(http.request.uri.path, "/.well-known/")'


def build_worker_script(fingerprints):
    src = WORKER_JS.read_text()
    assert src.count(PLACEHOLDER) == 1, "worker.js placeholder missing"
    return src.replace(PLACEHOLDER, json.dumps(list(fingerprints)))
