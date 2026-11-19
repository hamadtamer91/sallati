"""Vision LLM clients. The pipeline depends only on the VisionLLM protocol, so the provider is swappable."""
from __future__ import annotations

import base64
import json
import os
import re
import threading
from typing import Any, Callable, Protocol


class VisionLLM(Protocol):
    calls: int

    def extract(self, image: bytes, media_type: str, instruction: str, schema: dict) -> dict: ...


def _loads(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


class AnthropicVisionLLM:
    """Claude vision with structured outputs (output_config.format = json_schema).

    Needs ANTHROPIC_API_KEY in the environment. Model via VISION_MODEL (default claude-sonnet-5-5)."""

    def __init__(self, model: str | None = None, max_tokens: int = 8000, client: Any = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(max_retries=4)
        self.client = client
        self.model = model or os.environ.get("VISION_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self._lock = threading.Lock()

    def extract(self, image: bytes, media_type: str, instruction: str, schema: dict) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                 "data": base64.b64encode(image).decode()}},
                    {"type": "text", "text": instruction},
                ],
            }],
            # passed through extra_body so it works across SDK versions
            extra_body={"output_config": {"format": {"type": "json_schema", "schema": schema}}},
        )
        with self._lock:
            self.calls += 1
            usage = getattr(resp, "usage", None)
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise ValueError("vision response truncated (max_tokens); use smaller tiles")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        return _loads(text)


class FakeVisionLLM:
    """Deterministic stand-in for tests: responder(instruction, schema, image) -> dict."""

    def __init__(self, responder: Callable[[str, dict, bytes], dict]):
        self.responder = responder
        self.calls = 0

    def extract(self, image: bytes, media_type: str, instruction: str, schema: dict) -> dict:
        self.calls += 1
        return self.responder(instruction, schema, image)
