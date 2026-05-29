import httpx
import json
import time
from typing import Optional, Tuple
from trispoke.config import get_settings

settings = get_settings()

class OllamaClient:
    def __init__(self):
        self.host = settings.ollama_host
        self.default_model = settings.ollama_model

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 600
    ) -> Tuple[str, int, float]:
        """Generate text using Ollama"""
        if model is None:
            model = self.default_model

        start_time = time.time()

        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{self.host}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": True,
                        # Disable thinking-mode for reasoning models (qwen3, etc.).
                        # Without this, the entire num_predict budget can be spent
                        # inside <think>…</think> and `response` comes back empty.
                        "think": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_tokens,
                        },
                    },
                    timeout=120.0,
                )
                response.raise_for_status()

                full_response = ""
                tokens_used = 0

                # Stream the response
                for line in response.iter_lines():
                    if line:
                        data = json.loads(line)
                        if "response" in data:
                            full_response += data["response"]
                        if data.get("done"):
                            tokens_used = data.get("eval_count", 0)
                            break

                elapsed = time.time() - start_time
                return full_response, tokens_used, elapsed

        except httpx.ConnectError:
            raise Exception(
                "Ollama is not running. Start it with: ollama serve"
            )