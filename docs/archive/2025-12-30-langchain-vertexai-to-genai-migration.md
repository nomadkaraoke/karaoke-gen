# LangChain Vertex AI to Google GenAI Migration

**Date**: 2025-12-30
**PR**: #145

## Problem

Cloud job `b1b2648c` hung silently during agentic correction. No logs appeared after "LangFuse prompt service initialized". Investigation revealed `ChatVertexAI` (langchain-google-vertexai) was hanging during gRPC connection establishment with no timeout.

## Solution

Migrated from `langchain-google-vertexai` to `langchain-google-genai`:

| Before | After |
|--------|-------|
| `ChatVertexAI` (gRPC-based) | `ChatGoogleGenerativeAI` (REST-based) |
| Vertex AI service account auth | GOOGLE_API_KEY (AI Studio) |
| No initialization timeout | 30s init + 15s warm-up timeouts |
| DEBUG-level logs | INFO-level logs with timing |

## Key Changes

### Dependencies
```toml
# Before
langchain-google-vertexai = ">=3.1.1"

# After
langchain-google-genai = ">=2.0.0"
```

### Model Creation
```python
# Before (gRPC, can hang)
from langchain_google_vertexai import ChatVertexAI
model = ChatVertexAI(model=model_name, project=project, location=location)

# After (REST, explicit API key)
from langchain_google_genai import ChatGoogleGenerativeAI
model = ChatGoogleGenerativeAI(
    model=model_name,
    google_api_key=api_key,
    convert_system_message_to_human=True,  # Gemini doesn't support system messages
)
```

### Timeout Implementation
Used `ThreadPoolExecutor` for cross-platform timeout (works on Windows, Linux, macOS):

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(self._factory.create_chat_model, model, config)
    try:
        self._chat_model = future.result(timeout=30)
    except FuturesTimeoutError:
        raise InitializationTimeoutError("Model initialization timed out")
```

### Warm-up Pattern
Added lightweight "warm-up" request after model creation to establish connection before real work:

```python
warm_up_prompt = 'Respond with exactly: {"status":"ready"}'
response = model.invoke([HumanMessage(content=warm_up_prompt)])
```

## Files Modified

| File | Changes |
|------|---------|
| `pyproject.toml` | Replaced dependency, bumped version |
| `lyrics_transcriber_temp/pyproject.toml` | Same |
| `.../providers/config.py` | Added `initialization_timeout_seconds`, `warmup_timeout_seconds` |
| `.../providers/model_factory.py` | Migrated to `ChatGoogleGenerativeAI`, added timing logs |
| `.../providers/langchain_bridge.py` | Added ThreadPoolExecutor timeout wrapper, warm-up, INFO logs |
| `.../agent.py` | Added timing logs for classify_gap |

## Why REST over gRPC

1. **Simpler debugging** - HTTP requests are easier to trace than gRPC streams
2. **No connection pooling issues** - REST is stateless
3. **Better timeout behavior** - HTTP timeouts are well-understood
4. **Cloud-friendly** - Works better through proxies and load balancers

## Authentication

The new approach uses Google AI Studio API keys instead of Vertex AI service accounts:

- **Get key**: https://aistudio.google.com/app/apikey
- **Set env var**: `GOOGLE_API_KEY=<your-key>`
- **Benefits**: Simpler setup, no IAM configuration needed

Note: This uses Google AI Studio quotas, not GCP billing. For high-volume production use, consider keeping Vertex AI with proper timeout handling.
