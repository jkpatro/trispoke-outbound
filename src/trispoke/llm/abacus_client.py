"""Abacus.AI RouteLLM client.

OpenAI-compatible endpoint. Same `(text, tokens_used, seconds_elapsed)` return
shape as `OllamaClient` and `ClaudeClient` so the router can treat all three
engines interchangeably.

Errors are normalised into two flavours the router cares about:
  - `AbacusAuthError`  → 401/403 (missing / invalid key)
  - `AbacusRateLimitError` → 429 after retries exhausted
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

from openai import (
    APIStatusError,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from trispoke.config import get_settings


class AbacusAuthError(Exception):
    """Raised when Abacus rejects the API key."""


class AbacusRateLimitError(Exception):
    """Raised when Abacus 429s persist after the retry budget is exhausted."""


_RETRY_BACKOFF_SECONDS = (2, 4, 8)  # 3 attempts total


class AbacusClient:
    """Thin wrapper around the OpenAI SDK pointed at Abacus RouteLLM."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.abacus_api_key:
            raise AbacusAuthError("ABACUS_API_KEY missing or invalid")
        self.api_key = settings.abacus_api_key
        self.base_url = settings.abacus_base_url
        self.default_model = settings.abacus_model
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 600,
        stream: bool = False,
    ) -> Tuple[str, int, float]:
        """Generate a completion. Returns (text, tokens_used, seconds_elapsed)."""
        chosen_model = model or self.default_model
        start = time.time()

        for attempt, backoff in enumerate((*_RETRY_BACKOFF_SECONDS, None)):
            try:
                if stream:
                    text, tokens = self._generate_streaming(
                        chosen_model, prompt, temperature, max_tokens
                    )
                else:
                    text, tokens = self._generate_blocking(
                        chosen_model, prompt, temperature, max_tokens
                    )
                return text, tokens, time.time() - start
            except (AuthenticationError, PermissionDeniedError) as e:
                raise AbacusAuthError("ABACUS_API_KEY missing or invalid") from e
            except RateLimitError:
                if backoff is None:
                    raise AbacusRateLimitError(
                        f"Abacus rate-limited after {len(_RETRY_BACKOFF_SECONDS) + 1} attempts"
                    )
                time.sleep(backoff)
                continue
            except APIStatusError as e:
                if e.status_code in (401, 403):
                    raise AbacusAuthError("ABACUS_API_KEY missing or invalid") from e
                if e.status_code == 429:
                    if backoff is None:
                        raise AbacusRateLimitError(
                            f"Abacus rate-limited after {len(_RETRY_BACKOFF_SECONDS) + 1} attempts"
                        ) from e
                    time.sleep(backoff)
                    continue
                raise

        # Unreachable: the for-loop either returns or raises on every path.
        raise RuntimeError("Abacus retry loop fell through unexpectedly")

    # ---------- request shapes ----------

    def _generate_blocking(
        self, model: str, prompt: str, temperature: float, max_tokens: int
    ) -> Tuple[str, int]:
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
        tokens = getattr(resp.usage, "total_tokens", 0) or 0
        return text, tokens

    def _generate_streaming(
        self, model: str, prompt: str, temperature: float, max_tokens: int
    ) -> Tuple[str, int]:
        stream = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        chunks: list[str] = []
        tokens = 0
        for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            if delta and delta.content:
                chunks.append(delta.content)
            usage = getattr(event, "usage", None)
            if usage:
                tokens = getattr(usage, "total_tokens", tokens) or tokens
        return "".join(chunks), tokens
