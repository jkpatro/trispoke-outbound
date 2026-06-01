import anthropic
import time
from typing import Tuple, Optional
from trispoke.config import get_settings
from trispoke.secrets_store import secret

settings = get_settings()

class ClaudeClient:
    def __init__(self):
        key = secret("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        if not key:
            raise Exception("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic(api_key=key)

    def generate(
        self,
        prompt: str,
        model: str = "claude-sonnet-4-6",
        temperature: float = 0.7,
        max_tokens: int = 600
    ) -> Tuple[str, Optional[int], float]:
        """Generate text using Claude"""
        start_time = time.time()

        message = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system="You are a helpful assistant.",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        response_text = message.content[0].text
        tokens_used = getattr(message.usage, 'input_tokens', None)  # Approximate
        elapsed = time.time() - start_time

        return response_text, tokens_used, elapsed