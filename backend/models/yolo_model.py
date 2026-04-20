"""YOLO adapter — real-time object detection via ultralytics.

Setup steps
-----------
1.  pip install ultralytics
    (This pulls in torch/torchvision automatically if not already installed.)

2.  The default model is ``yolov8n.pt`` (YOLOv8 Nano — fastest).  Override
    via ``YOLO_MODEL`` env var.  Options include:
      - yolov8n.pt, yolov8s.pt, yolov8m.pt, yolov8l.pt, yolov8x.pt
      - yolov5su.pt, yolov5mu.pt
      - yolo11n.pt (YOLO11)
      - /path/to/your/custom_trained.pt

3.  For UI-element detection, fine-tune YOLO on a labelled desktop
    screenshot dataset.  Use ``yolo train data=ui_dataset.yaml model=yolov8n.pt``
    then set ``YOLO_MODEL=/path/to/best.pt``.

4.  Set ``YOLO_CONFIDENCE`` (default 0.25) to control detection threshold.
"""

from __future__ import annotations

import io
import os
from typing import Any

from .base import VisionModel
from .registry import model_registry

_MODEL_PATH = os.getenv("YOLO_MODEL", "yolov8n.pt")
_CONFIDENCE = float(os.getenv("YOLO_CONFIDENCE", "0.25"))


def _load_model():
    from ultralytics import YOLO
    return YOLO(_MODEL_PATH)


class YOLOModel(VisionModel):
    name = "yolo"

    def __init__(self):
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            self._model = _load_model()
        return self._model

    def _detect(self, screenshot_png: bytes) -> list[dict[str, Any]]:
        from PIL import Image

        model = self._ensure_model()
        img = Image.open(io.BytesIO(screenshot_png)).convert("RGB")
        results = model.predict(img, conf=_CONFIDENCE, verbose=False)

        detections: list[dict[str, Any]] = []
        for result in results:
            for box in result.boxes:
                xyxy = box.xyxy[0].tolist()
                detections.append({
                    "box": xyxy,
                    "cx": int((xyxy[0] + xyxy[2]) / 2),
                    "cy": int((xyxy[1] + xyxy[3]) / 2),
                    "confidence": float(box.conf[0]),
                    "class_id": int(box.cls[0]),
                    "class_name": result.names[int(box.cls[0])],
                })

        detections.sort(key=lambda d: d["confidence"], reverse=True)
        return detections

    def detect_element(self, screenshot_png: bytes, intent: str, monitor_bounds=None) -> dict[str, Any]:
        detections = self._detect(screenshot_png)

        if not detections:
            return {
                "x": 0, "y": 0,
                "action_type": "click",
                "text_to_type": "",
                "reason": "No objects detected.",
            }

        # Simple heuristic: pick the top-confidence detection.
        # With a fine-tuned model and proper class labels, you'd match
        # against `intent` by class_name here.
        best = detections[0]
        return {
            "x": best["cx"],
            "y": best["cy"],
            "action_type": "click",
            "text_to_type": "",
            "reason": f"Detected '{best['class_name']}' (conf={best['confidence']:.2f})",
        }

    def plan_action(self, screenshot_png: bytes, prompt: str, monitor_bounds=None) -> dict[str, Any]:
        result = self.detect_element(screenshot_png, prompt, monitor_bounds)
        return {
            "action": "click",
            "x": result["x"],
            "y": result["y"],
            "reason": result.get("reason", "YOLO detection"),
        }

    def analyze(self, screenshot_png: bytes, prompt: str) -> dict[str, Any]:
        detections = self._detect(screenshot_png)
        return {"detections": detections, "count": len(detections)}


model_registry("yolo", YOLOModel)
