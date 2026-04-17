"""
FastAPI backend for the Windows Agent.

Provides:
- REST endpoint /api/status — returns the current agent state.
- REST endpoint /api/intervene — accepts (x, y) coordinates from the frontend.
- WebSocket endpoint /ws/screen — streams live screenshots to the frontend.
"""

import asyncio
import base64
from enum import Enum

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import mss
import mss.tools
import pyautogui

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Windows Agent Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
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


class InterveneResponse(BaseModel):
    message: str
    x: int
    y: int
    action: str

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

    execute_action(payload.x, payload.y, action=action)

    message = "Mouse clicked" if action == "click" else "Mouse moved"
    return InterveneResponse(
        message=message,
        x=payload.x,
        y=payload.y,
        action=action,
    )

# ---------------------------------------------------------------------------
# WebSocket — live screen feed
# ---------------------------------------------------------------------------

SCREEN_FPS = 3  # Target frames per second


@app.websocket("/ws/screen")
async def screen_feed(websocket: WebSocket):
    """Stream base64-encoded screenshots of the primary monitor."""
    await websocket.accept()
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            while True:
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


def execute_action(x: int, y: int, action: str = "click") -> None:
    """
    Execute an OS-level mouse/keyboard action at the given coordinates.

    Currently supports a simple left-click. Extend this function to handle
    additional actions (double-click, right-click, typing, etc.) as needed.
    """
    if action == "move":
        pyautogui.moveTo(x, y)
        return

    pyautogui.moveTo(x, y)
    pyautogui.click(x, y)
