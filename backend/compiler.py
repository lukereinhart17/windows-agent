import base64
import json
import os
import re
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-1.5-flash"

if not API_KEY:
    raise ValueError("Missing Gemini API key. Set GOOGLE_API_KEY or GEMINI_API_KEY in .env.")

genai.configure(api_key=API_KEY)

SYSTEM_PROMPT = """
You are an expert automation engineer.
You will receive an ordered set of UI interaction steps.
Each step contains:
- A screenshot image.
- Raw action metadata, including click coordinates and any provided action fields.

Your job:
1) Inspect each screenshot and metadata.
2) Infer the likely user intent for that action in plain language.
3) Return only strict JSON as an array of objects in this exact shape:
[
  {"step": 1, "action": "click", "intent": "Click the 'Save' button in Aspire"}
]

Rules:
- Output must be valid JSON only. No prose, no markdown, no code fences.
- Keep step numbering aligned with the input order.
- Preserve the best action label based on provided metadata. If unknown, use "click".
- Intent should be concise and specific to visible UI context.
""".strip()


def _strip_markdown_fences(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_base64_image(step: dict[str, Any]) -> str:
    candidates = (
        step.get("screenshot"),
        step.get("screenshot_base64"),
        step.get("image"),
        step.get("image_base64"),
        step.get("frame"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            value = candidate.strip()
            if "base64," in value:
                return value.split("base64,", 1)[1]
            return value
    raise ValueError("Each step must include a non-empty base64 screenshot field.")


def _extract_action(step: dict[str, Any]) -> str:
    action = step.get("action")
    if isinstance(action, str) and action.strip():
        return action.strip().lower()
    return "click"


def _extract_coordinates(step: dict[str, Any]) -> dict[str, Any]:
    if isinstance(step.get("coordinates"), dict):
        return step["coordinates"]
    coords: dict[str, Any] = {}
    if "x" in step:
        coords["x"] = step["x"]
    if "y" in step:
        coords["y"] = step["y"]
    if "raw" in step:
        coords["raw"] = step["raw"]
    return coords


def generate_semantic_sop(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert screenshot + coordinate/action steps into semantic SOP JSON using Gemini 1.5 Flash.
    """
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list.")

    model = genai.GenerativeModel(
        MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
    )

    parts: list[Any] = [
        (
            "Analyze the following ordered steps and return strict JSON only. "
            "Do not wrap the JSON in markdown fences."
        )
    ]

    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError("Each step must be a dictionary.")

        image_b64 = _extract_base64_image(step)
        image_bytes = base64.b64decode(image_b64, validate=True)

        action = _extract_action(step)
        coordinates = _extract_coordinates(step)

        parts.append(
            f"Step {i} metadata:\n"
            f"- action: {action}\n"
            f"- coordinates/raw: {json.dumps(coordinates, ensure_ascii=False)}"
        )
        parts.append({"mime_type": "image/png", "data": image_bytes})

    response = model.generate_content(parts)
    raw_text = response.text if hasattr(response, "text") else ""
    if not raw_text:
        raise ValueError("Gemini returned an empty response.")

    cleaned = _strip_markdown_fences(raw_text)
    parsed = json.loads(cleaned)

    if not isinstance(parsed, list):
        raise ValueError("Gemini response must be a JSON array.")

    normalized: list[dict[str, Any]] = []
    for i, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError("Each output item must be a JSON object.")
        normalized.append(
            {
                "step": int(item.get("step", i)),
                "action": str(item.get("action", "click")),
                "intent": str(item.get("intent", "")).strip(),
            }
        )

    return normalized
