"""
ai_client.py — Centralised OpenAI wrapper with retries, logging, and JSON repair.
"""

from __future__ import annotations
import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.config import OUTPUT_LOGS
from src.utils import save_json, timestamp_str

load_dotenv()
log = logging.getLogger("boq.ai_client")

_client = None
_api_available = False


def _get_client():
    global _client, _api_available
    if _client is not None:
        return _client
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "your_real_key_here":
        log.warning("OPENAI_API_KEY not set — AI features disabled")
        _api_available = False
        return None
    try:
        from openai import OpenAI
        _client = OpenAI(api_key=api_key)
        _api_available = True
        return _client
    except ImportError:
        log.warning("openai package not installed — AI features disabled")
        _api_available = False
        return None


def is_available() -> bool:
    return _get_client() is not None


SYSTEM_PROMPT_MASTER = """You are a SENIOR QUANTITY SURVEYOR and BOQ REVIEWER with deep experience in:
- residential construction
- light-gauge steel framing / Framecad systems
- Australian and PNG housing projects
- drawing review, take-off checking, schedules, and BOQ verification

Core behaviour rules:
1. Accuracy is more important than completeness.
2. Extract only what is explicitly visible or clearly stated.
3. Do not guess hidden quantities.
4. Do not invent missing dimensions, counts, materials, or schedules.
5. If data is unclear, return null or "unclear" and explain why.
6. If different parts of the drawings conflict, flag the conflict clearly.
7. Where a schedule exists, treat the schedule as more authoritative than visual counting.
8. Distinguish carefully between explicit drawing data, derived/calculated data, and assumptions.
9. Always think like an estimator preparing a BOQ reviewed by a senior professional.
10. Output must be structured, conservative, and auditable.

Confidence levels:
- HIGH = explicit and clearly readable on drawing/schedule/BOM
- MEDIUM = explicit but partially unclear, or confirmed by only one imperfect source
- LOW = derived from rules, fallback assumptions, or weakly readable content"""


def call_json(
    user_prompt: str,
    system_prompt: str = SYSTEM_PROMPT_MASTER,
    images: list[Path] | None = None,
    model: str = "gpt-4o",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    retries: int = 3,
    label: str = "ai_call",
) -> dict | list | None:
    """Call OpenAI, return parsed JSON.  Saves raw response to logs on failure."""
    client = _get_client()
    if client is None:
        return None

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Build user content — text + optional images
    if images:
        content: list[dict] = [{"type": "text", "text": user_prompt}]
        for img_path in images:
            try:
                data = base64.b64encode(img_path.read_bytes()).decode()
                ext = img_path.suffix.lower().lstrip(".")
                mime = f"image/{ext}" if ext in ("png", "jpg", "jpeg", "gif", "webp") else "image/png"
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"},
                })
            except Exception as exc:
                log.warning("Could not encode image %s: %s", img_path, exc)
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_prompt})

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            raw = response.choices[0].message.content or ""
            _save_raw(label, raw)
            parsed = _parse_json(raw)
            if parsed is not None:
                return parsed
            # Try repair
            repaired = repair_json(raw)
            if repaired is not None:
                return repaired
            log.warning("[%s] JSON parse failed on attempt %d", label, attempt)
        except Exception as exc:
            last_err = exc
            log.warning("[%s] API error attempt %d: %s", label, attempt, exc)
            if attempt < retries:
                time.sleep(2 ** attempt)

    log.error("[%s] All attempts failed. Last error: %s", label, last_err)
    return None


def _parse_json(text: str) -> dict | list | None:
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def repair_json(raw: str) -> dict | list | None:
    """Best-effort JSON extraction from partially-valid text."""
    # Try to find outermost { } or [ ]
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = raw.find(start_char)
        end = raw.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def encode_image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _save_raw(label: str, text: str) -> None:
    try:
        out = OUTPUT_LOGS / f"{label}_{timestamp_str()}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    except Exception:
        pass
