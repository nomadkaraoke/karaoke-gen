# GCE encode worker — golden response fixtures

Real-shaped responses returned by the GCE encoding worker's `encode_videos`
(the value `GCEEncodingBackend.encode()` consumes), captured/authored as golden
JSON so the encode→publish seam is tested against the *actual* contract rather
than hand-authored per-test mocks. Each conforms to the `GceEncodeResponse`
TypedDict in `backend/services/encoding_interface.py`.

| Fixture | Shape | What it exercises |
|---|---|---|
| `success.json` | `complete`, all 4 requested formats | Happy path. Uses the NOMAD-1632 title ("Portrait of Jennie") so classification must key off the trailing format tag, not the title text. |
| `cached.json` | `complete`, full set incl. portrait/with-vocals/title/end/CDG | A cached hit returning every additive final; exercises every classifier branch. |
| `empty.json` | `complete`, `output_files: []` | Stale/cached-but-deleted job. Must be treated as **recoverable** (re-encode), NOT a partial/defective result. This shape caused Failure B. |
| `partial.json` | `complete`, missing `mp4_720p` | Failure A: the completeness guard must trip and refuse to publish. |
| `malformed.json` | `complete`, stray / wrong-extension names | Nothing classifies into a slot → treated as empty (not crash, not mis-slotted). |

Replayed by `backend/tests/test_encoding_contract.py` (contract test) and driven
through the real orchestrator by `TestEncodePublishSeam` in
`backend/tests/test_video_worker_orchestrator.py`.
