from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VisionModel(ABC):
    """Abstract base for all vision models used by the agent.

    Every concrete model must implement three methods:
      - detect_element:  locate a UI element from a screenshot + intent string
      - plan_action:     given a user prompt + screenshot, decide what action to take
      - analyze:         free-form screenshot analysis (return structured JSON)

    Concrete implementations live in sibling modules and register themselves
    with the ``model_registry`` at import time.
    """

    # Human-readable name shown in the frontend model selector.
    name: str = "base"

    @abstractmethod
    def detect_element(
        self,
        screenshot_png: bytes,
        intent: str,
        monitor_bounds: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Locate a UI element described by *intent* in *screenshot_png*.

        Returns a dict with at minimum::

            {"x": int, "y": int, "action_type": "click"|"type"|"scroll",
             "text_to_type": ""}
        """

    @abstractmethod
    def plan_action(
        self,
        screenshot_png: bytes,
        prompt: str,
        monitor_bounds: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Decide a single UI action for *prompt* given the current screen.

        Returns::

            {"action": "click"|"move", "x": int, "y": int, "reason": str}
        """

    @abstractmethod
    def analyze(
        self,
        screenshot_png: bytes,
        prompt: str,
    ) -> dict[str, Any]:
        """Free-form image analysis.  Returns an arbitrary JSON-serialisable dict."""
