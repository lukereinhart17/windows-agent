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
import json
import os
from enum import Enum

import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from .executor import execute_task
except ImportError:
    from executor import execute_task
import mss
import mss.tools
import pyautogui

# ---------------------------------------------------------------------------
# Environment & Gemini configuration
# ---------------------------------------------------------------------------

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    import warnings
    warnings.warn(
        "GEMINI_API_KEY is not set. The /api/analyze-screen endpoint will not work.",
        stacklevel=2,
    )
else:
    genai.configure(api_key=GEMINI_API_KEY)

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


class PromptResponse(BaseModel):
    reply: str


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


# ---------------------------------------------------------------------------
# Chat context (in-memory)
# ---------------------------------------------------------------------------

chat_history: list[dict] = []
recorded_actions: list[dict] = []

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


@app.post("/api/prompt", response_model=PromptResponse)
async def send_prompt(payload: PromptRequest):
    """Accept a user prompt and store it in chat history."""
    chat_history.append({"role": "user", "content": payload.message, "screenshot": None})
    # Placeholder reply — replace with Gemini integration when ready
    reply = f"Received: {payload.message}"
    chat_history.append({"role": "assistant", "content": reply, "screenshot": None})
    return PromptResponse(reply=reply)


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


@app.post("/api/analyze-screen")
async def analyze_screen(payload: AnalyzeScreenRequest):
    """
    Accept a base64 encoded image and a text prompt, send them to the
    Gemini 1.5 Flash model, and return the model's structured JSON response.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is not configured. Set it in your .env file.",
        )

    # Strip optional data-URI prefix (e.g. "data:image/png;base64,...")
    image_data = payload.image
    if "base64," in image_data:
        image_data = image_data.split("base64,", 1)[1]

    image_part = {
        "mime_type": "image/png",
        "data": base64.b64decode(image_data),
    }

    model = genai.GenerativeModel("gemini-1.5-flash")

    try:
        response = model.generate_content(
            [payload.prompt, image_part],
            generation_config={"response_mime_type": "application/json"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc

    raw_text = (response.text or "").strip()
    if not raw_text:
        raise HTTPException(status_code=502, detail="Gemini returned an empty response.")

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini returned invalid JSON: {raw_text}",
        ) from exc

    return result


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
        execute_task(task_name, monitor_index=selected_monitor_index)
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
    global_x = int(bounds.left + x)
    global_y = int(bounds.top + y)

    if action == "move":
        pyautogui.moveTo(global_x, global_y)
        return

    pyautogui.moveTo(global_x, global_y)
    pyautogui.click(global_x, global_y)
