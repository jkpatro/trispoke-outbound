"""Tests for the Abacus.AI RouteLLM client and the hybrid_smart fall-through.

Mocks the openai SDK at the import boundary so no network calls happen.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import AuthenticationError, RateLimitError


# ---------- helpers ----------


def _fake_completion(text: str = "hello", total_tokens: int = 42) -> SimpleNamespace:
    """Build the minimal object shape AbacusClient.generate reads."""
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg)
    usage = SimpleNamespace(total_tokens=total_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://routellm.abacus.ai/v1/chat/completions")


def _fake_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_fake_request())


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Default to an Abacus-configured environment for every test in this file.

    Tests that need missing-key behaviour set ABACUS_API_KEY to "" explicitly.
    """
    monkeypatch.setenv("ABACUS_API_KEY", "test-key-123")
    monkeypatch.setenv("ABACUS_BASE_URL", "https://routellm.abacus.ai/v1")
    monkeypatch.setenv("ABACUS_MODEL", "route-llm")
    # Settings is lru_cache'd — clear so monkeypatched env is re-read.
    from trispoke.config import get_settings

    get_settings.cache_clear()


# ---------- AbacusClient ----------


def test_generate_returns_text_tokens_seconds():
    from trispoke.llm.abacus_client import AbacusClient

    with patch("trispoke.llm.abacus_client.OpenAI") as mock_openai:
        instance = mock_openai.return_value
        instance.chat.completions.create.return_value = _fake_completion(
            "Subject: hi\n\nDear X,\n\nBody.\n\nBest,\nA", total_tokens=120
        )

        client = AbacusClient()
        text, tokens, elapsed = client.generate("the prompt")

    assert "Dear X" in text
    assert tokens == 120
    assert elapsed >= 0.0


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.setenv("ABACUS_API_KEY", "")
    from trispoke.config import get_settings

    get_settings.cache_clear()
    from trispoke.llm.abacus_client import AbacusAuthError, AbacusClient

    with pytest.raises(AbacusAuthError, match="ABACUS_API_KEY missing or invalid"):
        AbacusClient()


def test_401_maps_to_abacus_auth_error():
    from trispoke.llm.abacus_client import AbacusAuthError, AbacusClient

    with patch("trispoke.llm.abacus_client.OpenAI") as mock_openai:
        instance = mock_openai.return_value
        instance.chat.completions.create.side_effect = AuthenticationError(
            message="bad key",
            response=_fake_response(401),
            body=None,
        )

        client = AbacusClient()
        with pytest.raises(AbacusAuthError):
            client.generate("the prompt")


def test_429_retries_three_times_then_raises(monkeypatch):
    """Should attempt 4 calls total (initial + 3 backoffs) before giving up."""
    from trispoke.llm.abacus_client import AbacusClient, AbacusRateLimitError

    # Skip the real sleeps so the test is fast.
    monkeypatch.setattr("trispoke.llm.abacus_client.time.sleep", lambda *_: None)

    with patch("trispoke.llm.abacus_client.OpenAI") as mock_openai:
        instance = mock_openai.return_value
        instance.chat.completions.create.side_effect = RateLimitError(
            message="rate limited",
            response=_fake_response(429),
            body=None,
        )

        client = AbacusClient()
        with pytest.raises(AbacusRateLimitError):
            client.generate("the prompt")

    # initial attempt + 3 backoff retries = 4 calls
    assert instance.chat.completions.create.call_count == 4


def test_429_then_success(monkeypatch):
    """A single 429 should be retried and the next success returned."""
    from trispoke.llm.abacus_client import AbacusClient

    monkeypatch.setattr("trispoke.llm.abacus_client.time.sleep", lambda *_: None)

    with patch("trispoke.llm.abacus_client.OpenAI") as mock_openai:
        instance = mock_openai.return_value
        instance.chat.completions.create.side_effect = [
            RateLimitError(message="rate limited", response=_fake_response(429), body=None),
            _fake_completion("recovered", total_tokens=7),
        ]
        client = AbacusClient()
        text, tokens, _ = client.generate("the prompt")

    assert text == "recovered"
    assert tokens == 7
    assert instance.chat.completions.create.call_count == 2


# ---------- hybrid_smart fall-through ----------


def _patch_lead_and_pain(monkeypatch):
    """Minimal stand-ins for what router._generate consumes."""
    lead = SimpleNamespace(
        first_name="Test",
        last_name="User",
        title="Head of Eng",
        company_name="Acme",
        company_industry="SaaS",
        company_size="120",
    )
    pain = SimpleNamespace(
        chronic="ch", acute="ac", trigger="tr", confidence=0.5
    )
    return lead, pain


def _ok_response(text: str = "Subject: hello\n\nDear Test,\n\nBody.\n\nBest,\nA"):
    """Pre-formatted response that the router's _extract_* helpers accept."""
    return (text, 50, 1.0)


def test_hybrid_smart_uses_claude_when_claude_works(monkeypatch):
    from trispoke.llm.router import LLMRouter

    lead, pain = _patch_lead_and_pain(monkeypatch)

    fake_claude = MagicMock()
    fake_claude.generate.return_value = _ok_response()
    fake_abacus = MagicMock()
    fake_abacus.generate.return_value = _ok_response("Subject: from-abacus\n\nDear T,\n\nB.\n\nBest,\nA")
    fake_abacus.default_model = "route-llm"
    fake_ollama = MagicMock()
    fake_ollama.generate.return_value = _ok_response("Subject: from-local\n\nDear T,\n\nB.\n\nBest,\nA")
    fake_ollama.default_model = "qwen3:8b"

    router = LLMRouter()
    router.ollama = fake_ollama
    router._claude = fake_claude
    router._abacus = fake_abacus

    draft = router.generate_email(lead, pain, "hybrid_smart")

    assert draft.model_used.startswith("claude:")
    fake_claude.generate.assert_called_once()
    fake_abacus.generate.assert_not_called()
    fake_ollama.generate.assert_not_called()


def test_hybrid_smart_falls_through_to_abacus_when_claude_fails(monkeypatch):
    from trispoke.llm.router import LLMRouter

    lead, pain = _patch_lead_and_pain(monkeypatch)

    fake_claude = MagicMock()
    fake_claude.generate.side_effect = Exception("claude unavailable")
    fake_abacus = MagicMock()
    fake_abacus.generate.return_value = _ok_response("Subject: from-abacus\n\nDear T,\n\nB.\n\nBest,\nA")
    fake_abacus.default_model = "route-llm"
    fake_ollama = MagicMock()
    fake_ollama.generate.return_value = _ok_response("Subject: from-local\n\nDear T,\n\nB.\n\nBest,\nA")
    fake_ollama.default_model = "qwen3:8b"

    router = LLMRouter()
    router.ollama = fake_ollama
    router._claude = fake_claude
    router._abacus = fake_abacus

    draft = router.generate_email(lead, pain, "hybrid_smart")

    assert draft.model_used.startswith("abacus:")
    fake_claude.generate.assert_called_once()
    fake_abacus.generate.assert_called_once()
    fake_ollama.generate.assert_not_called()


def test_hybrid_smart_falls_through_to_local_when_claude_and_abacus_fail(monkeypatch):
    from trispoke.llm.router import LLMRouter

    lead, pain = _patch_lead_and_pain(monkeypatch)

    fake_claude = MagicMock()
    fake_claude.generate.side_effect = Exception("claude unavailable")
    fake_abacus = MagicMock()
    fake_abacus.generate.side_effect = Exception("abacus unavailable")
    fake_abacus.default_model = "route-llm"
    fake_ollama = MagicMock()
    fake_ollama.generate.return_value = _ok_response("Subject: from-local\n\nDear T,\n\nB.\n\nBest,\nA")
    fake_ollama.default_model = "qwen3:8b"

    router = LLMRouter()
    router.ollama = fake_ollama
    router._claude = fake_claude
    router._abacus = fake_abacus

    draft = router.generate_email(lead, pain, "hybrid_smart")

    assert draft.model_used.startswith("local:")
    fake_claude.generate.assert_called_once()
    fake_abacus.generate.assert_called_once()
    fake_ollama.generate.assert_called_once()


def test_legacy_hybrid_still_skips_abacus(monkeypatch):
    """Backward-compat: mode='hybrid' must NOT touch Abacus, even if configured."""
    from trispoke.llm.router import LLMRouter

    lead, pain = _patch_lead_and_pain(monkeypatch)

    fake_claude = MagicMock()
    fake_claude.generate.side_effect = Exception("claude unavailable")
    fake_abacus = MagicMock()  # would succeed, but should never be called
    fake_abacus.generate.return_value = _ok_response("Subject: from-abacus\n\nDear T,\n\nB.\n\nBest,\nA")
    fake_abacus.default_model = "route-llm"
    fake_ollama = MagicMock()
    fake_ollama.generate.return_value = _ok_response("Subject: from-local\n\nDear T,\n\nB.\n\nBest,\nA")
    fake_ollama.default_model = "qwen3:8b"

    router = LLMRouter()
    router.ollama = fake_ollama
    router._claude = fake_claude
    router._abacus = fake_abacus

    draft = router.generate_email(lead, pain, "hybrid")

    assert draft.model_used.startswith("local:")
    fake_abacus.generate.assert_not_called()
