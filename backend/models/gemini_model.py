"""Gemini vision model adapter — wraps the existing Google Generative AI integration."""

from __future__ import annotations

import json
import os
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv
from pathlib import Path

from .base import VisionModel
from .registry import model_registry

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for p in (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local"):
    if p.exists():
        load_dotenv(p, override=False)

_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_FALLBACK_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
)

if _API_KEY:
    genai.configure(api_key=_API_KEY)


def _model_candidates() -> list[str]:
    ordered = [_MODEL_NAME, *_FALLBACK_MODELS]
    seen: set[str] = set()
    unique: list[str] = []
    for name in ordered:
        n = name.strip()
        if n and n not in seen:
            unique.append(n)
            seen.add(n)
    return unique


def _is_model_unavailable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "model" in msg and ("not found" in msg or "no longer available" in msg or "not supported" in msg)


def _generate(contents: list, generation_config: dict | None = None):
    last_exc: Exception | None = None
    for name in _model_candidates():
        try:
            model = genai.GenerativeModel(name)
            return model.generate_content(contents, generation_config=generation_config)
        except Exception as exc:
            last_exc = exc
            if _is_model_unavailable(exc):
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("No Gemini models configured.")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class GeminiModel(VisionModel):
    name = "gemini"

    def detect_element(self, screenshot_png: bytes, intent: str, monitor_bounds=None) -> dict[str, Any]:
        prompt = (
            "You are an AI UI agent. Find the UI element described by this intent: "
            f"'{intent}'. Return the exact X and Y pixel coordinates of the center of that element on this screen. "
            'Return ONLY valid JSON in this format: {"x": int, "y": int, "action_type": "click|type|scroll", "text_to_type": "optional string"}.'
        )
        response = _generate(
            [prompt, {"mime_type": "image/png", "data": screenshot_png}],
            generation_config={"response_mime_type": "application/json"},
        )
        data = json.loads((response.text or "").strip())
        action_type = str(data.get("action_type", "click")).lower()
        if action_type not in {"click", "type", "scroll"}:
            action_type = "click"
        return {
            "x": int(data["x"]),
            "y": int(data["y"]),
            "action_type": action_type,
            "text_to_type": str(data.get("text_to_type", "")),
        }

    def plan_action(self, screenshot_png: bytes, prompt: str, monitor_bounds=None) -> dict[str, Any]:
        bounds = monitor_bounds or {}
        width = bounds.get("width", 1920)
        height = bounds.get("height", 1080)

        system_prompt = (
            "You are an AI agent controlling a Windows desktop via mouse actions. "
            "Locate the UI element the user describes, compute its bounding-box center, "
            "and return coordinates relative to the monitor screenshot (0,0 = top-left). "
            f"Screenshot dimensions: {width}x{height}. "
            f"User request: '{prompt}'. "
            'Return ONLY valid JSON: {{"action": "click|move", "x": int, "y": int, "reason": "short explanation"}}.'
        )
        response = _generate(
            [system_prompt, {"mime_type": "image/png", "data": screenshot_png}],
            generation_config={"response_mime_type": "application/json"},
        )
        data = json.loads((response.text or "").strip())
        action = str(data.get("action", "click")).lower()
        if action not in {"click", "move"}:
            action = "click"
        x = max(0, min(int(data.get("x", 0)), width - 1))
        y = max(0, min(int(data.get("y", 0)), height - 1))
        return {
            "action": action,
            "x": x,
            "y": y,
            "reason": str(data.get("reason", "")).strip(),
        }

    def analyze(self, screenshot_png: bytes, prompt: str) -> dict[str, Any]:
        response = _generate(
            [prompt, {"mime_type": "image/png", "data": screenshot_png}],
            generation_config={"response_mime_type": "application/json"},
        )
        raw = (response.text or "").strip()
        if not raw:
            raise ValueError("Gemini returned an empty response.")
        return json.loads(raw)


model_registry("gemini", GeminiModel)
