"""Agentic correction's Gemini provider uses the Gemini Developer API key, never Vertex."""
from unittest.mock import patch

import pytest

from karaoke_gen.lyrics_transcriber.correction.agentic.providers.config import ProviderConfig
from karaoke_gen.lyrics_transcriber.correction.agentic.providers.model_factory import ModelFactory
from karaoke_gen.lyrics_transcriber.correction.agentic.router import DEFAULT_CLOUD_MODEL


def _config(monkeypatch, **env):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")  # must NOT trigger Vertex
    return ProviderConfig.from_env()


def test_default_model_is_developer_api():
    assert DEFAULT_CLOUD_MODEL == "gemini/gemini-3.8-flash"


@pytest.mark.parametrize("spec", ["gemini/gemini-3.8-flash", "vertexai/gemini-3.8-flash"])
def test_gemini_model_uses_api_key_not_project(monkeypatch, spec):
    config = _config(monkeypatch, GEMINI_API_KEY="dev-key")
    with patch("langchain_google_genai.ChatGoogleGenerativeAI") as chat:
        factory = ModelFactory()
        provider, model_name = factory._parse_model_spec(spec)
        factory._instantiate_model(provider, model_name, [], config)
    kwargs = chat.call_args.kwargs
    assert kwargs["google_api_key"] == "dev-key"
    assert kwargs["model"] == "gemini-3.8-flash"
    assert "project" not in kwargs and "location" not in kwargs


def test_google_api_key_still_accepted(monkeypatch):
    config = _config(monkeypatch, GOOGLE_API_KEY="legacy-key")
    assert config.google_api_key == "legacy-key"


def test_missing_key_raises_clear_error(monkeypatch):
    config = _config(monkeypatch)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        ModelFactory()._create_gemini_model("gemini-3.8-flash", [], config)
