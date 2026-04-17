import json
import os
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai
import mss
import mss.tools
import pyautogui
from dotenv import load_dotenv

pyautogui.FAILSAFE = True

load_dotenv()

MODEL_NAME = "gemini-1.5-flash"
ACTION_DELAY_SECONDS = float(os.getenv("EXECUTOR_ACTION_DELAY_SECONDS", "2"))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = PROJECT_ROOT / "tasks"


def _build_model() -> genai.GenerativeModel:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Missing Gemini API key. Set GOOGLE_API_KEY or GEMINI_API_KEY in .env.")

    genai.configure(api_key=api_key)
    return genai.GenerativeModel(MODEL_NAME)


def _validate_task_name(task_name: str) -> str:
    cleaned = task_name.strip()
    if not cleaned:
        raise ValueError("task_name cannot be empty.")
    if any(part in {".", ".."} for part in cleaned.split("/")):
        raise ValueError("Invalid task_name.")
    if "\\" in cleaned:
        cleaned = cleaned.replace("\\", "/")
    return cleaned


def _task_file_path(task_name: str) -> Path:
    safe_name = _validate_task_name(task_name)
    path = (TASKS_DIR / f"{safe_name}.json").resolve()
    tasks_root = TASKS_DIR.resolve()
    if tasks_root not in path.parents:
        raise ValueError("Invalid task path.")
    return path


def _load_compiled_sop(task_name: str) -> list[dict[str, Any]]:
    path = _task_file_path(task_name)
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        steps = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("steps"), list):
            steps = payload["steps"]
        elif isinstance(payload.get("sop"), list):
            steps = payload["sop"]
        else:
            raise ValueError("Task JSON must contain a list or a 'steps'/'sop' array.")
    else:
        raise ValueError("Task JSON is invalid. Expected an array or object.")

    if not steps:
        raise ValueError("Task SOP is empty.")

    normalized: list[dict[str, Any]] = []
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"Step {i} must be an object.")
        intent = str(step.get("intent", "")).strip()
        if not intent:
            raise ValueError(f"Step {i} is missing an intent.")
        normalized.append(step)

    return normalized


def _capture_primary_monitor_png() -> bytes:
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        screenshot = sct.grab(monitor)
        return mss.tools.to_png(screenshot.rgb, screenshot.size)


def _resolve_monitor(monitor_index: int) -> dict[str, int]:
    with mss.mss() as sct:
        if len(sct.monitors) <= 1:
            return {"left": 0, "top": 0, "width": 1920, "height": 1080}

        if monitor_index < 1 or monitor_index >= len(sct.monitors):
            monitor_index = 1

        monitor = sct.monitors[monitor_index]
        return {
            "left": int(monitor["left"]),
            "top": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }


def _capture_monitor_png(monitor: dict[str, int]) -> bytes:
    with mss.mss() as sct:
        screenshot = sct.grab(monitor)
        return mss.tools.to_png(screenshot.rgb, screenshot.size)


def _gemini_step_action(model: genai.GenerativeModel, intent: str, screenshot_png: bytes) -> dict[str, Any]:
    prompt = (
        "You are an AI UI agent. Find the UI element described by this intent: "
        f"'{intent}'. Return the exact X and Y pixel coordinates of the center of that element on this screen. "
        "Return ONLY valid JSON in this format: {\"x\": int, \"y\": int, \"action_type\": \"click|type|scroll\", \"text_to_type\": \"optional string\"}."
    )

    response = model.generate_content(
        [
            prompt,
            {"mime_type": "image/png", "data": screenshot_png},
        ],
        generation_config={
            "response_mime_type": "application/json",
        },
    )

    raw_text = (response.text or "").strip()
    if not raw_text:
        raise ValueError("Gemini returned an empty response.")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {raw_text}") from exc

    if not isinstance(data, dict):
        raise ValueError("Gemini JSON response must be an object.")

    x = int(data["x"])
    y = int(data["y"])
    action_type = str(data.get("action_type", "click")).strip().lower()
    text_to_type = str(data.get("text_to_type", ""))

    if action_type not in {"click", "type", "scroll"}:
        action_type = "click"

    return {
        "x": x,
        "y": y,
        "action_type": action_type,
        "text_to_type": text_to_type,
    }


def _apply_action(action: dict[str, Any], monitor: dict[str, int]) -> None:
    x = int(action["x"])
    y = int(action["y"])
    global_x = int(monitor["left"] + x)
    global_y = int(monitor["top"] + y)
    action_type = str(action["action_type"]).lower()

    if action_type == "click":
        pyautogui.moveTo(global_x, global_y, duration=0.5)
        pyautogui.click()
    elif action_type == "type":
        pyautogui.moveTo(global_x, global_y, duration=0.5)
        pyautogui.click()
        pyautogui.write(str(action.get("text_to_type", "")), interval=0.05)
    elif action_type == "scroll":
        pyautogui.moveTo(global_x, global_y, duration=0.5)
        pyautogui.scroll(-500)
    else:
        pyautogui.moveTo(global_x, global_y, duration=0.5)
        pyautogui.click()


def execute_task(task_name: str, monitor_index: int = 1) -> None:
    steps = _load_compiled_sop(task_name)
    model = _build_model()
    monitor = _resolve_monitor(monitor_index)

    for step in steps:
        intent = str(step["intent"]).strip()
        screenshot_png = _capture_monitor_png(monitor)
        action = _gemini_step_action(model, intent, screenshot_png)
        _apply_action(action, monitor)
        time.sleep(ACTION_DELAY_SECONDS)
