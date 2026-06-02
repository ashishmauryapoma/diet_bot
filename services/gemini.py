"""
services/gemini.py — Gemini REST API (no google-generativeai SDK, no protobuf)
Calls https://generativelanguage.googleapis.com directly via httpx.
"""
import base64
import json
import logging
import time
from collections import deque

import httpx

import config

logger = logging.getLogger(__name__)

# ── Rate limiter (5 calls / 60 s) ──────────────────────────────────────────────
_call_times: deque = deque()

def _check_rate_limit() -> None:
    now = time.time()
    while _call_times and now - _call_times[0] > 60:
        _call_times.popleft()
    if len(_call_times) >= config.GEMINI_MAX_CALLS_PER_MINUTE:
        raise RuntimeError("Rate limit: too many Gemini calls. Wait a moment and try again.")
    _call_times.append(now)

# ── Base URL ────────────────────────────────────────────────────────────────────
_BASE = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{config.GEMINI_MODEL}:generateContent"
)

TEXT_SYSTEM = (
    "You are a certified nutritionist AI. Extract nutritional data accurately. "
    "Always respond ONLY with valid JSON. No markdown, no explanation, no extra text."
)

VISION_SYSTEM = (
    "You are a nutritionist AI with expert food vision analysis. "
    "Identify all food items in the image, estimate portions visually. "
    "Return ONLY valid JSON, no markdown, no extra text."
)

TEXT_TEMPLATE = """{system}

The user ate: "{user_input}"

Return this exact JSON:
{{
  "food_name": "string",
  "calories": integer,
  "protein_g": float,
  "carbs_g": float,
  "fat_g": float,
  "fiber_g": float,
  "serving_size": "string (e.g. '2 medium rotis ~120g')",
  "meal_components": ["item1", "item2"],
  "data_confidence": "high | medium | low",
  "notes": "string or null"
}}"""

VISION_TEMPLATE = """{system}

Return this exact JSON describing the food in the image:
{{
  "food_name": "string (combined name of the meal)",
  "calories": integer,
  "protein_g": float,
  "carbs_g": float,
  "fat_g": float,
  "fiber_g": float,
  "serving_description": "string (visual estimate, e.g. '1 medium bowl ~300g')",
  "identified_items": ["item1", "item2"],
  "confidence": "high | medium | low",
  "notes": "string or null"
}}

If the image does not contain food, return:
{{ "error": "no_food_detected", "message": "I couldn't find any food in this image." }}"""


def _parse_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON."""
    clean = raw.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1]) if len(lines) > 2 else clean
    return json.loads(clean)


def _extract_text(response: dict) -> str:
    """Pull the text out of a Gemini REST response."""
    try:
        return response["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response shape: {response}") from e


async def _post(payload: dict) -> dict:
    """Async POST to Gemini REST endpoint."""
    url = f"{_BASE}?key={config.GEMINI_API_KEY}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


async def analyze_food_text(user_input: str) -> dict:
    """Analyse food described in text. Returns parsed dict."""
    _check_rate_limit()
    prompt = TEXT_TEMPLATE.format(system=TEXT_SYSTEM, user_input=user_input)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    response = await _post(payload)
    return _parse_json(_extract_text(response))


async def analyze_food_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Analyse food from image bytes. Returns parsed dict."""
    _check_rate_limit()
    b64 = base64.b64encode(image_bytes).decode()
    prompt = VISION_TEMPLATE.format(system=VISION_SYSTEM)
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": b64}},
                {"text": prompt},
            ]
        }]
    }
    response = await _post(payload)
    return _parse_json(_extract_text(response))


async def re_analyze_food(original_food: str, correction: str) -> dict:
    """Re-analyse a food entry given a correction."""
    combined = f"{original_food} — correction: {correction}"
    return await analyze_food_text(combined)
