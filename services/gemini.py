"""
services/gemini.py — Gemini API integration for text + vision food analysis
"""
import asyncio
import base64
import json
import logging
import time
from collections import deque
from typing import Optional

import google.generativeai as genai

import config

logger = logging.getLogger(__name__)

# ── Rate limiter ────────────────────────────────────────────────────────────────
_call_times: deque = deque()

def _check_rate_limit() -> None:
    now = time.time()
    while _call_times and now - _call_times[0] > 60:
        _call_times.popleft()
    if len(_call_times) >= config.GEMINI_MAX_CALLS_PER_MINUTE:
        raise RuntimeError("Rate limit: too many Gemini calls. Wait a moment and try again.")
    _call_times.append(now)

# ── Initialise ──────────────────────────────────────────────────────────────────
genai.configure(api_key=config.GEMINI_API_KEY)
_model = genai.GenerativeModel(config.GEMINI_MODEL)

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


async def analyze_food_text(user_input: str) -> dict:
    """Analyze food described in text. Returns parsed dict."""
    _check_rate_limit()
    prompt = TEXT_TEMPLATE.format(system=TEXT_SYSTEM, user_input=user_input)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _model.generate_content, prompt)
    return _parse_json(response.text)


async def analyze_food_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Analyze food from image bytes. Returns parsed dict."""
    _check_rate_limit()
    b64 = base64.b64encode(image_bytes).decode()
    prompt = VISION_TEMPLATE.format(system=VISION_SYSTEM)
    contents = [
        {"mime_type": mime_type, "data": b64},
        prompt,
    ]
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _model.generate_content, contents)
    return _parse_json(response.text)


async def re_analyze_food(original_food: str, correction: str) -> dict:
    """Re-analyze a food entry given a correction."""
    combined = f"{original_food} — correction: {correction}"
    return await analyze_food_text(combined)
