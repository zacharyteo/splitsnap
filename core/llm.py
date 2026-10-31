"""Nosana inference via its OpenAI-compatible endpoint.

NOSANA_BASE_URL can point at any OpenAI-compatible server while the Nosana job
boots; the README states honestly which endpoint served the demo.
"""
from __future__ import annotations

import json
import os
import re
import time

from openai import OpenAI

_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        base = os.environ.get("NOSANA_BASE_URL", "").rstrip("/")
        if not base:
            raise RuntimeError("NOSANA_BASE_URL is not set. Put your Nosana endpoint (ending in /v1) in .env")
        if not base.endswith("/v1"):
            base += "/v1"
        _client = OpenAI(base_url=base, api_key=os.environ.get("NOSANA_API_KEY") or "x",
                         timeout=float(os.environ.get("LLM_TIMEOUT_S", "180")), max_retries=1)
    return _client


def text_model() -> str:
    m = os.environ.get("NOSANA_TEXT_MODEL")
    if m:
        return m
    return client().models.list().data[0].id  # first served model


def vision_model() -> str | None:
    return os.environ.get("NOSANA_VISION_MODEL") or None


def chat(messages: list[dict], model: str | None = None, max_tokens: int = 3000) -> tuple[str, str, float]:
    """Returns (text, model_name, seconds)."""
    model = model or text_model()
    t = time.time()
    try:
        r = client().chat.completions.create(model=model, messages=messages,
                                             temperature=0.1, max_tokens=max_tokens)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Nosana LLM call failed ({type(e).__name__}): {e}. "
                           "Check the job is still running and NOSANA_BASE_URL is right.") from e
    return r.choices[0].message.content or "", model, time.time() - t


def parse_json(text: str):
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    cand = m.group(1) if m else text
    start = cand.find("{")
    if start < 0:
        raise ValueError("no JSON object in model reply")
    depth = 0
    for i, ch in enumerate(cand[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            return json.loads(cand[start:i + 1])
    raise ValueError("unterminated JSON object in model reply")


def parse_code(text: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def health() -> tuple[bool, str]:
    try:
        ids = [m.id for m in client().models.list().data]
        return True, ", ".join(ids[:4])
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:120]}"
