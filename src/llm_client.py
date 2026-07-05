"""
llm_client.py — OpenAI-compatible wrapper for the RunPod vLLM endpoint.

Provides:
  load_config(path)                — YAML config with ${ENV_VAR} resolution
  LLMClient.call_llm(...)          — retried chat completion, optional JSON mode
  count_tokens(text)               — tiktoken-based estimate
  chunk_text(text, max_tokens, overlap)

vLLM serves the OpenAI Chat Completions API, so the official `openai`
client works unchanged; only base_url/api_key differ.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import tiktoken
import yaml
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

# tiktoken has no Llama tokenizer; cl100k_base over-counts Llama text by
# roughly 10-15%, which is a safe direction for budget math. The encoding
# file is downloaded on first use; if that fails (offline/air-gapped), we
# fall back to a conservative ~3.5-chars-per-token heuristic instead of
# crashing at import time.
_encoder = None
_encoder_failed = False


class _CharFallbackEncoder:
    """Approximate tokenizer used only when tiktoken's BPE file is unavailable."""

    CHARS_PER_TOKEN = 3.5

    def encode(self, text: str, disallowed_special=()) -> list[int]:
        n = max(1, int(len(text) / self.CHARS_PER_TOKEN)) if text else 0
        return list(range(n))

    def decode(self, tokens: list[int]) -> str:  # only meaningful for real tokens
        raise NotImplementedError


def _get_encoder():
    global _encoder, _encoder_failed
    if _encoder is None and not _encoder_failed:
        try:
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # noqa: BLE001 — network/cache failure
            logger.warning(
                "tiktoken encoding unavailable (%s); falling back to a "
                "character-based token estimate. Chunk boundaries will be "
                "approximate but budget-safe.", exc,
            )
            _encoder_failed = True
    return _encoder


def _resolve_env(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders with environment values."""
    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            var = m.group(1)
            resolved = os.environ.get(var)
            if resolved is None:
                raise RuntimeError(
                    f"Config references ${{{var}}} but it is not set. "
                    f"Copy .env.example to .env and fill it in."
                )
            return resolved
        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def load_config(path: str | Path = "config.yaml") -> dict:
    """Load YAML config, resolving ${ENV_VAR} placeholders from .env/environment."""
    load_dotenv()
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return _resolve_env(raw)


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = log_cfg.get("file")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def count_tokens(text: str) -> int:
    encoder = _get_encoder()
    if encoder is None:
        return len(_CharFallbackEncoder().encode(text))
    return len(encoder.encode(text, disallowed_special=()))


def _chunk_by_chars(text: str, max_tokens: int, overlap: int) -> list[str]:
    """Fallback chunker: character windows sized by the chars-per-token estimate."""
    ratio = _CharFallbackEncoder.CHARS_PER_TOKEN
    max_chars, overlap_chars = int(max_tokens * ratio), int(overlap * ratio)
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        window = text[start:end]
        if end < len(text):
            cut = window.rfind("\n\n", int(len(window) * 0.8))
            if cut != -1:
                window, end = window[:cut], start + cut
        if window.strip():
            chunks.append(window.strip())
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def chunk_text(text: str, max_tokens: int = 8000, overlap: int = 400) -> list[str]:
    """
    Token-window chunking with overlap. Prefers paragraph boundaries when a
    boundary exists inside the last 20% of the window; otherwise hard-cuts.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap >= max_tokens:
        raise ValueError("overlap must be smaller than max_tokens")

    encoder = _get_encoder()
    if encoder is None:
        return _chunk_by_chars(text, max_tokens, overlap)

    tokens = encoder.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return [text] if text.strip() else []

    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        window = encoder.decode(tokens[start:end])
        if end < len(tokens):
            tail = window[int(len(window) * 0.8):]
            cut = tail.rfind("\n\n")
            if cut != -1:
                window = window[: int(len(window) * 0.8) + cut]
                end = start + len(encoder.encode(window, disallowed_special=()))
        if window.strip():
            chunks.append(window.strip())
        if end >= len(tokens):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _strip_json_fences(content: str) -> str:
    """Some models wrap JSON in ``` fences despite instructions; strip them."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content.strip()


class LLMClient:
    """Thin, retried wrapper around the vLLM OpenAI-compatible endpoint."""

    def __init__(self, config: dict):
        runpod = config["runpod"]
        self.model: str = runpod["model"]
        self.default_max_tokens: int = int(
            config.get("batching", {}).get("tokens_per_request", 4096)
        )
        self._client = OpenAI(
            base_url=runpod["base_url"],
            api_key=runpod["api_key"],
            timeout=180.0,
        )

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _completion(
        self,
        messages: list[dict],
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> str:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if not content:
            raise APIConnectionError(request=None)  # retryable: empty completion
        return content

    def call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        json_mode: bool = True,
        temperature: float = 0.0,
    ) -> dict | str:
        """
        Send one chat completion. With json_mode=True, returns a parsed dict
        (raises ValueError on unparseable output so callers can count the
        failure and move on). Otherwise returns raw text.
        """
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        content = self._completion(
            messages=messages,
            max_tokens=max_tokens or self.default_max_tokens,
            json_mode=json_mode,
            temperature=temperature,
        )
        if not json_mode:
            return content
        try:
            return json.loads(_strip_json_fences(content))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Model returned non-JSON content: {exc}") from exc
