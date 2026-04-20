"""
FastAPI backend for the Windows Agent.

Provides:
- REST endpoint /api/status — returns the current agent state.
- REST endpoint /api/intervene — accepts (x, y) coordinates from the frontend.
- REST endpoint /api/execute/{task_name} — starts autonomous SOP execution.
- WebSocket endpoint /ws/screen — streams live screenshots to the frontend.
"""

import asyncio
import base64
import ctypes
import json
import os
import re
from enum import Enum
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from .executor import execute_task
except ImportError:
    from executor import execute_task

try:
    from .models import get_active_model, get_model, set_active_model, list_models
    from .models.registry import model_registry as _reg
except ImportError:
    from models import get_active_model, get_model, set_active_model, list_models
    from models.registry import model_registry as _reg

try:
    from .pipeline import (
        PipelineConfig,
        config_to_dict,
        detect_step_with_pipeline,
        plan_action_with_pipeline,
    )
except ImportError:
    from pipeline import (
        PipelineConfig,
        config_to_dict,
        detect_step_with_pipeline,
        plan_action_with_pipeline,
    )

import mss
import mss.tools
import pyautogui

# ---------------------------------------------------------------------------
# Environment & Gemini configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATHS = (
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / ".env.local",
    PROJECT_ROOT / "frontend" / ".env",
    PROJECT_ROOT / "frontend" / ".env.local",
)

for env_path in ENV_PATHS:
    if env_path.exists():
        load_dotenv(env_path, override=False)

GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
)
if not GEMINI_API_KEY:
    import warnings
    warnings.warn(
        "GEMINI_API_KEY is not set. The /api/analyze-screen endpoint will not work.",
        stacklevel=2,
    )
else:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------------------------
# Register all vision model adapters (lazy imports — each adapter only
# pulls in heavy deps like torch/ultralytics when actually instantiated).
# ---------------------------------------------------------------------------

def _register_all_models() -> None:
    """Import every adapter module so it registers itself with the registry."""
    import importlib
    adapter_modules = [
        "models.gemini_model",
        "models.faster_rcnn",
        "models.resnet_efficientnet",
        "models.mobilenet_shufflenet",
        "models.yolo_model",
        "models.cnnparted_model",
    ]
    for mod_name in adapter_modules:
        try:
            importlib.import_module(f".{mod_name}" if __package__ else mod_name, __package__)
        except Exception:
            pass  # adapter stays unregistered if deps are missing

_register_all_models()

# Default to gemini if API key is available
if GEMINI_API_KEY:
    try:
        set_active_model("gemini")
    except KeyError:
        pass

def _model_candidates() -> list[str]:
    ordered = [GEMINI_MODEL, *FALLBACK_MODELS]
    seen: set[str] = set()
    unique: list[str] = []
    for name in ordered:
        normalized = name.strip()
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


def _is_model_unavailable_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "model" in msg
        and (
            "not found" in msg
            or "no longer available" in msg
            or "not supported" in msg
        )
    )


def _generate_content_with_fallback(contents, generation_config=None):
    last_exc: Exception | None = None
    for model_name in _model_candidates():
        try:
            model = genai.GenerativeModel(model_name)
            return model.generate_content(contents, generation_config=generation_config)
        except Exception as exc:
            last_exc = exc
            if _is_model_unavailable_error(exc):
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("No Gemini models configured.")

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Windows Agent Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    REQUIRES_INTERVENTION = "requires_intervention"


agent_status: AgentStatus = AgentStatus.IDLE

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    status: AgentStatus


class InterveneRequest(BaseModel):
    x: int
    y: int
    action: str = "click"
    monitor_index: int | None = None


class InterveneResponse(BaseModel):
    message: str
    x: int
    y: int
    action: str


class ExecuteTaskResponse(BaseModel):
    message: str
    task_name: str
    status: AgentStatus


class MonitorInfo(BaseModel):
    mss_index: int
    display_index: int
    label: str
    left: int
    top: int
    width: int
    height: int


class MonitorListResponse(BaseModel):
    selected_monitor_index: int
    monitors: list[MonitorInfo]


class SetMonitorRequest(BaseModel):
    monitor_index: int


class SetMonitorResponse(BaseModel):
    message: str
    selected_monitor_index: int


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    screenshot: str | None = None  # base64 PNG, optional


class PromptRequest(BaseModel):
    message: str
    monitor_index: int | None = None


class PromptResponse(BaseModel):
    reply: str
    debug: dict | None = None


class RecordedAction(BaseModel):
    x: int
    y: int
    action: str
    monitor_index: int
    screenshot: str  # base64 PNG at time of action


class RecordActionRequest(BaseModel):
    x: int
    y: int
    action: str = "click"
    monitor_index: int


class RecordActionResponse(BaseModel):
    message: str
    recorded_action: RecordedAction


class ScreenshotResponse(BaseModel):
    screenshot: str  # base64 PNG
    monitor_index: int


class AnalyzeScreenRequest(BaseModel):
    image: str  # base64 encoded image string
    prompt: str


class CalibrationOffset(BaseModel):
    monitor_index: int
    offset_x: int
    offset_y: int


class CalibrationComputeRequest(BaseModel):
    monitor_index: int
    target_x: int
    target_y: int
    actual_x: int
    actual_y: int


class CalibrationListResponse(BaseModel):
    selected_monitor_index: int
    offsets: list[CalibrationOffset]


class SetModelRequest(BaseModel):
    name: str


class PipelineConfigRequest(BaseModel):
    mode: str
    detector_model: str | None = None
    classifier_model: str | None = None
    planner_model: str | None = None
    verify_before_click: bool | None = None
    verification_threshold: float | None = None
    fallback_single_on_low_confidence: bool | None = None
    verifier_model: str | None = None


# ---------------------------------------------------------------------------
# Chat context (in-memory)
# ---------------------------------------------------------------------------

chat_history: list[dict] = []
recorded_actions: list[dict] = []
pipeline_config = PipelineConfig()


def _enable_windows_dpi_awareness() -> None:
    """Enable DPI awareness so screenshot and cursor coordinates use the same pixel space."""
    if os.name != "nt":
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _load_initial_offsets() -> dict[int, tuple[int, int]]:
    """Load optional per-monitor offsets from JSON in CLICK_OFFSETS_JSON."""
    raw = os.getenv("CLICK_OFFSETS_JSON", "").strip()
    if not raw:
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    parsed: dict[int, tuple[int, int]] = {}
    for key, value in payload.items():
        try:
            monitor_index = int(key)
            if isinstance(value, dict):
                offset_x = int(value.get("x", 0))
                offset_y = int(value.get("y", 0))
            elif isinstance(value, list) and len(value) >= 2:
                offset_x = int(value[0])
                offset_y = int(value[1])
            else:
                continue
            parsed[monitor_index] = (offset_x, offset_y)
        except (TypeError, ValueError):
            continue

    return parsed


_enable_windows_dpi_awareness()

# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    """Return the current agent status."""
    return StatusResponse(status=agent_status)


@app.post("/api/intervene", response_model=InterveneResponse)
async def intervene(payload: InterveneRequest):
    """
    Receive (x, y) coordinates from the frontend and execute a mouse action
    at that position.
    """
    action = payload.action.lower()
    if action not in {"click", "move"}:
        action = "click"

    execute_action(
        payload.x,
        payload.y,
        action=action,
        monitor_index=payload.monitor_index,
    )

    message = "Mouse clicked" if action == "click" else "Mouse moved"
    return InterveneResponse(
        message=message,
        x=payload.x,
        y=payload.y,
        action=action,
    )


is_execution_running = False
selected_monitor_index = 1
monitor_offsets: dict[int, tuple[int, int]] = _load_initial_offsets()


def _available_monitors() -> list[MonitorInfo]:
    with mss.mss() as sct:
        raw_monitors: list[tuple[int, dict]] = [
            (idx, monitor) for idx, monitor in enumerate(sct.monitors) if idx > 0
        ]
        # Keep IDs stable using mss indices, but present displays in left-to-right order.
        ordered = sorted(raw_monitors, key=lambda item: (item[1]["left"], item[1]["top"]))

        monitors: list[MonitorInfo] = []
        for display_idx, (mss_idx, monitor) in enumerate(ordered, start=1):
            monitors.append(
                MonitorInfo(
                    mss_index=mss_idx,
                    display_index=display_idx,
                    label=(
                        f"Display {display_idx}"
                        f" ({int(monitor['width'])}x{int(monitor['height'])}, "
                        f"x={int(monitor['left'])}, y={int(monitor['top'])})"
                    ),
                    left=int(monitor["left"]),
                    top=int(monitor["top"]),
                    width=int(monitor["width"]),
                    height=int(monitor["height"]),
                )
            )
        return monitors


def _resolve_monitor_index(requested_index: int) -> int:
    monitors = _available_monitors()
    if not monitors:
        return 1
    valid_indices = {m.mss_index for m in monitors}
    if requested_index in valid_indices:
        return requested_index
    return monitors[0].mss_index


def _monitor_bounds(monitor_index: int) -> MonitorInfo:
    resolved = _resolve_monitor_index(monitor_index)
    for monitor in _available_monitors():
        if monitor.mss_index == resolved:
            return monitor
    return MonitorInfo(
        mss_index=1,
        display_index=1,
        label="Display 1 (1920x1080, x=0, y=0)",
        left=0,
        top=0,
        width=1920,
        height=1080,
    )


def _get_monitor_offset(monitor_index: int) -> tuple[int, int]:
    resolved = _resolve_monitor_index(monitor_index)
    return monitor_offsets.get(resolved, (0, 0))


def _set_monitor_offset(monitor_index: int, offset_x: int, offset_y: int) -> CalibrationOffset:
    resolved = _resolve_monitor_index(monitor_index)
    monitor_offsets[resolved] = (int(offset_x), int(offset_y))
    x, y = monitor_offsets[resolved]
    return CalibrationOffset(monitor_index=resolved, offset_x=x, offset_y=y)


@app.get("/api/monitors", response_model=MonitorListResponse)
async def get_monitors():
    """List detected monitors and current active monitor index."""
    global selected_monitor_index
    selected_monitor_index = _resolve_monitor_index(selected_monitor_index)
    return MonitorListResponse(
        selected_monitor_index=selected_monitor_index,
        monitors=_available_monitors(),
    )


@app.post("/api/monitor", response_model=SetMonitorResponse)
async def set_monitor(payload: SetMonitorRequest):
    """Select which monitor should be used for viewing and execution."""
    global selected_monitor_index
    selected_monitor_index = _resolve_monitor_index(payload.monitor_index)
    return SetMonitorResponse(
        message="Active monitor updated",
        selected_monitor_index=selected_monitor_index,
    )


@app.get("/api/calibration", response_model=CalibrationListResponse)
async def get_calibration():
    """Return all active per-monitor coordinate offsets."""
    global selected_monitor_index
    selected_monitor_index = _resolve_monitor_index(selected_monitor_index)

    offsets = [
        CalibrationOffset(monitor_index=idx, offset_x=x, offset_y=y)
        for idx, (x, y) in sorted(monitor_offsets.items())
    ]

    return CalibrationListResponse(
        selected_monitor_index=selected_monitor_index,
        offsets=offsets,
    )


@app.post("/api/calibration", response_model=CalibrationOffset)
async def set_calibration(payload: CalibrationOffset):
    """Set absolute per-monitor pixel offset correction."""
    return _set_monitor_offset(payload.monitor_index, payload.offset_x, payload.offset_y)


@app.post("/api/calibration/compute", response_model=CalibrationOffset)
async def compute_calibration(payload: CalibrationComputeRequest):
    """Compute and apply offset from expected target vs actual click location."""
    delta_x = int(payload.target_x - payload.actual_x)
    delta_y = int(payload.target_y - payload.actual_y)
    current_x, current_y = _get_monitor_offset(payload.monitor_index)
    return _set_monitor_offset(payload.monitor_index, current_x + delta_x, current_y + delta_y)


@app.delete("/api/calibration/{monitor_index}", response_model=CalibrationOffset)
async def clear_calibration(monitor_index: int):
    """Clear per-monitor offset correction and return the cleared value."""
    resolved = _resolve_monitor_index(monitor_index)
    monitor_offsets.pop(resolved, None)
    return CalibrationOffset(monitor_index=resolved, offset_x=0, offset_y=0)


# ---------------------------------------------------------------------------
# Prompt / Chat endpoints
# ---------------------------------------------------------------------------


def _capture_monitor_b64(monitor_index: int) -> str:
    resolved = _resolve_monitor_index(monitor_index)
    with mss.mss() as sct:
        monitor = sct.monitors[resolved]
        screenshot = sct.grab(monitor)
        png_bytes = mss.tools.to_png(screenshot.rgb, screenshot.size)
        return base64.b64encode(png_bytes).decode("utf-8")


def _parse_direct_action(message: str) -> tuple[str, int, int] | None:
    """Parse direct commands like 'click 120 340' or 'move to 120, 340'."""
    pattern = re.compile(
        r"^\s*(click|move)\s*(?:to|at)?\s*(\d+)\s*[,\s]\s*(\d+)\s*$",
        re.IGNORECASE,
    )
    match = pattern.match(message)
    if not match:
        return None

    action = match.group(1).lower()
    x = int(match.group(2))
    y = int(match.group(3))
    return action, x, y


def _plan_action_from_prompt(message: str, monitor_index: int) -> dict:
    """Use configured pipeline to plan a single UI action."""
    if pipeline_config.mode == "single" and get_active_model() is None:
        raise ValueError(
            "No active model selected. "
            "Select one via POST /api/models/set or switch pipeline mode to cascade."
        )

    monitor = _monitor_bounds(monitor_index)
    screenshot_b64 = _capture_monitor_b64(monitor_index)
    screenshot_png = base64.b64decode(screenshot_b64)

    bounds = {
        "left": monitor.left,
        "top": monitor.top,
        "width": monitor.width,
        "height": monitor.height,
    }

    result = plan_action_with_pipeline(screenshot_png, message, bounds, pipeline_config)
    plan = result.plan

    x = max(0, min(int(plan.get("x", 0)), monitor.width - 1))
    y = max(0, min(int(plan.get("y", 0)), monitor.height - 1))
    action = str(plan.get("action", "click")).lower()
    if action not in {"click", "move"}:
        action = "click"

    return {
        "action": action,
        "x": x,
        "y": y,
        "reason": str(plan.get("reason", "")).strip(),
        "debug": result.debug,
    }


@app.post("/api/prompt", response_model=PromptResponse)
async def send_prompt(payload: PromptRequest):
    """Accept a user prompt, plan an action, and execute it when possible."""
    global selected_monitor_index
    debug = None

    chat_history.append({"role": "user", "content": payload.message, "screenshot": None})

    prompt_monitor_index = _resolve_monitor_index(
        payload.monitor_index if payload.monitor_index is not None else selected_monitor_index
    )

    try:
        direct_action = _parse_direct_action(payload.message)
        if direct_action is not None:
            action, x, y = direct_action
            execute_action(x, y, action=action, monitor_index=prompt_monitor_index)
            reply = f"Executed {action} at ({x}, {y}) on display {prompt_monitor_index}."
        elif get_active_model() is not None or pipeline_config.mode == "cascade":
            plan = _plan_action_from_prompt(payload.message, prompt_monitor_index)
            execute_action(
                plan["x"],
                plan["y"],
                action=plan["action"],
                monitor_index=prompt_monitor_index,
            )
            active_name = get_active_model().name if get_active_model() else pipeline_config.planner_model
            reply = (
                f"[{active_name}] Executed {plan['action']} at ({plan['x']}, {plan['y']}) "
                f"on display {prompt_monitor_index}. {plan['reason']}"
            )
            debug = plan.get("debug")
        else:
            reply = (
                "Prompt received, but no vision model is active. "
                "Select a model via the Model selector or set GOOGLE_API_KEY for Gemini. "
                "Direct commands like 'click 500 320' / 'move 200 100' still work."
            )
            debug = None
    except Exception as exc:
        reply = f"Failed to execute prompt: {exc}"
        debug = None

    chat_history.append({"role": "assistant", "content": reply, "screenshot": None})
    return PromptResponse(reply=reply, debug=debug)


@app.get("/api/chat")
async def get_chat():
    """Return current chat history including recorded actions."""
    return {"messages": chat_history}


@app.delete("/api/chat")
async def clear_chat():
    """Clear chat history and recorded actions."""
    chat_history.clear()
    recorded_actions.clear()
    return {"message": "Chat cleared"}


@app.get("/api/screenshot", response_model=ScreenshotResponse)
async def get_screenshot():
    """Take a screenshot of the active monitor and return as base64."""
    global selected_monitor_index
    b64 = _capture_monitor_b64(selected_monitor_index)
    return ScreenshotResponse(screenshot=b64, monitor_index=selected_monitor_index)


@app.get("/api/models")
async def get_models():
    """List all registered vision models and which is active."""
    return {"models": list_models()}


@app.post("/api/models/set")
async def set_model_endpoint(payload: SetModelRequest):
    """Switch the active vision model by name."""
    try:
        model = set_active_model(payload.name)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Active model set to '{model.name}'", "active": model.name}


@app.get("/api/pipeline")
async def get_pipeline_config():
    """Return current action pipeline config."""
    return {"pipeline": config_to_dict(pipeline_config)}


@app.post("/api/pipeline")
async def set_pipeline_config(payload: PipelineConfigRequest):
    """Configure action pipeline mode and participating models."""
    mode = payload.mode.lower().strip()
    if mode not in {"single", "cascade"}:
        raise HTTPException(status_code=400, detail="mode must be 'single' or 'cascade'.")

    # Validate configured models exist before saving.
    if payload.detector_model:
        try:
            get_model(payload.detector_model)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.classifier_model:
        try:
            get_model(payload.classifier_model)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.planner_model:
        try:
            get_model(payload.planner_model)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.verifier_model:
        try:
            get_model(payload.verifier_model)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.verification_threshold is not None:
        if payload.verification_threshold < 0.0 or payload.verification_threshold > 1.0:
            raise HTTPException(status_code=400, detail="verification_threshold must be between 0.0 and 1.0")

    pipeline_config.mode = mode
    if payload.detector_model:
        pipeline_config.detector_model = payload.detector_model
    if payload.classifier_model:
        pipeline_config.classifier_model = payload.classifier_model
    if payload.planner_model:
        pipeline_config.planner_model = payload.planner_model
    if payload.verify_before_click is not None:
        pipeline_config.verify_before_click = payload.verify_before_click
    if payload.verification_threshold is not None:
        pipeline_config.verification_threshold = float(payload.verification_threshold)
    if payload.fallback_single_on_low_confidence is not None:
        pipeline_config.fallback_single_on_low_confidence = payload.fallback_single_on_low_confidence
    if payload.verifier_model:
        pipeline_config.verifier_model = payload.verifier_model

    return {"message": "Pipeline updated", "pipeline": config_to_dict(pipeline_config)}


@app.post("/api/analyze-screen")
async def analyze_screen(payload: AnalyzeScreenRequest):
    """
    Accept a base64 encoded image and a text prompt, send them to the
    active vision model, and return the model's structured JSON response.
    """
    if pipeline_config.mode == "single" and get_active_model() is None:
        raise HTTPException(
            status_code=500,
            detail="No vision model is active. Select one via POST /api/models/set.",
        )

    # Strip optional data-URI prefix
    image_data = payload.image
    if "base64," in image_data:
        _, image_data = image_data.split("base64,", 1)

    image_bytes = base64.b64decode(image_data)

    try:
        bounds = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        result = plan_action_with_pipeline(image_bytes, payload.prompt, bounds, pipeline_config)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Model error: {exc}") from exc

    return {"plan": result.plan, "debug": result.debug}


@app.post("/api/record-action", response_model=RecordActionResponse)
async def record_action(payload: RecordActionRequest):
    """Record a user action with a screenshot and add to chat context."""
    monitor_idx = _resolve_monitor_index(payload.monitor_index)
    b64 = _capture_monitor_b64(monitor_idx)

    action_entry = RecordedAction(
        x=payload.x,
        y=payload.y,
        action=payload.action,
        monitor_index=monitor_idx,
        screenshot=b64,
    )
    recorded_actions.append(action_entry.model_dump())

    # Add to chat context so the agent can see what was recorded
    chat_history.append({
        "role": "user",
        "content": f"[Recorded action] {payload.action} at ({payload.x}, {payload.y}) on display {monitor_idx}",
        "screenshot": b64,
    })

    return RecordActionResponse(
        message="Action recorded",
        recorded_action=action_entry,
    )


@app.get("/api/recorded-actions")
async def get_recorded_actions():
    """Return all recorded actions for the current session."""
    return {"actions": recorded_actions}


def _run_task(task_name: str) -> None:
    global agent_status
    global is_execution_running
    global selected_monitor_index

    try:
        execute_task(
            task_name,
            monitor_index=selected_monitor_index,
            vision_model=get_active_model(),
            pipeline_config=pipeline_config,
        )
        agent_status = AgentStatus.IDLE
    except Exception:
        agent_status = AgentStatus.REQUIRES_INTERVENTION
        raise
    finally:
        is_execution_running = False


@app.post("/api/execute/{task_name}", response_model=ExecuteTaskResponse)
async def execute_task_endpoint(task_name: str, background_tasks: BackgroundTasks):
    """Start autonomous execution for a saved SOP in the background."""
    global agent_status
    global is_execution_running

    if is_execution_running:
        raise HTTPException(status_code=409, detail="Another execution is already running.")

    is_execution_running = True
    agent_status = AgentStatus.RUNNING
    background_tasks.add_task(_run_task, task_name)

    return ExecuteTaskResponse(
        message="Execution started",
        task_name=task_name,
        status=agent_status,
    )

# ---------------------------------------------------------------------------
# WebSocket — live screen feed
# ---------------------------------------------------------------------------

SCREEN_FPS = 3  # Target frames per second


@app.websocket("/ws/screen")
async def screen_feed(websocket: WebSocket):
    """Stream base64-encoded screenshots of the selected monitor."""
    global selected_monitor_index
    await websocket.accept()
    try:
        with mss.mss() as sct:
            while True:
                monitor_idx = _resolve_monitor_index(selected_monitor_index)
                monitor = sct.monitors[monitor_idx]
                screenshot = sct.grab(monitor)
                # Convert to PNG bytes
                png_bytes = mss.tools.to_png(screenshot.rgb, screenshot.size)
                b64_data = base64.b64encode(png_bytes).decode("utf-8")
                await websocket.send_text(b64_data)
                await asyncio.sleep(1 / SCREEN_FPS)
    except WebSocketDisconnect:
        pass

# ---------------------------------------------------------------------------
# OS-level action execution (placeholder)
# ---------------------------------------------------------------------------


def execute_action(
    x: int,
    y: int,
    action: str = "click",
    monitor_index: int | None = None,
) -> None:
    """
    Execute an OS-level mouse/keyboard action at the given coordinates.

    Currently supports a simple left-click. Extend this function to handle
    additional actions (double-click, right-click, typing, etc.) as needed.
    """
    target_monitor = monitor_index if monitor_index is not None else selected_monitor_index
    bounds = _monitor_bounds(target_monitor)
    offset_x, offset_y = _get_monitor_offset(target_monitor)

    adjusted_x = max(0, min(int(x + offset_x), max(0, bounds.width - 1)))
    adjusted_y = max(0, min(int(y + offset_y), max(0, bounds.height - 1)))

    global_x = int(bounds.left + adjusted_x)
    global_y = int(bounds.top + adjusted_y)

    if action == "move":
        pyautogui.moveTo(global_x, global_y)
        return

    pyautogui.click(global_x, global_y)
