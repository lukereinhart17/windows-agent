"""Faster R-CNN adapter — object detection via torchvision.

Setup steps
-----------
1.  pip install torch torchvision
    (use the CUDA wheel for GPU: https://pytorch.org/get-started/locally/)

2.  The default model (fasterrcnn_resnet50_fpn_v2) ships with COCO class
    weights.  For UI-element detection you will need to fine-tune on a
    labelled dataset of desktop screenshots.  The ``_load_model()`` helper
    will attempt to load a custom checkpoint from the path set in the
    ``FASTER_RCNN_CHECKPOINT`` env var.

3.  Set ``FASTER_RCNN_CHECKPOINT=/path/to/best.pth`` in your .env to use a
    fine-tuned model, or omit it to use COCO-pretrained weights (useful for
    smoke-testing the pipeline).

4.  Set ``FASTER_RCNN_LABELS=button,textbox,icon,...`` to define custom
    class names that map to your fine-tuned model's output indices.
"""

from __future__ import annotations

import io
import os
from typing import Any

from .base import VisionModel
from .registry import model_registry

_CHECKPOINT = os.getenv("FASTER_RCNN_CHECKPOINT", "")
_SCORE_THRESHOLD = float(os.getenv("FASTER_RCNN_SCORE_THRESH", "0.5"))
_LABELS = os.getenv("FASTER_RCNN_LABELS", "").split(",") if os.getenv("FASTER_RCNN_LABELS") else []


def _load_model():
    import torch
    import torchvision
    from torchvision.models.detection import fasterrcnn_resnet50_fpn_v2, FasterRCNN_ResNet50_FPN_V2_Weights

    if _CHECKPOINT and os.path.isfile(_CHECKPOINT):
        num_classes = max(len(_LABELS), 2)  # at least background + 1
        model = fasterrcnn_resnet50_fpn_v2(num_classes=num_classes)
        model.load_state_dict(torch.load(_CHECKPOINT, map_location="cpu"))
    else:
        model = fasterrcnn_resnet50_fpn_v2(weights=FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT)

    model.eval()
    return model


def _png_to_tensor(png_bytes: bytes):
    import torch
    from torchvision import transforms
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    return transforms.ToTensor()(img)


def _best_detection(predictions: dict, intent: str) -> dict[str, Any]:
    """Pick the highest-confidence detection matching *intent* (or just the top one)."""
    boxes = predictions["boxes"]
    scores = predictions["scores"]
    labels = predictions["labels"]

    if len(scores) == 0:
        return {"x": 0, "y": 0, "action_type": "click", "text_to_type": "", "reason": "No detections found."}

    best_idx = scores.argmax().item()
    box = boxes[best_idx].tolist()  # [x1, y1, x2, y2]
    cx = int((box[0] + box[2]) / 2)
    cy = int((box[1] + box[3]) / 2)
    label_idx = int(labels[best_idx].item())
    label_name = _LABELS[label_idx] if label_idx < len(_LABELS) else f"class_{label_idx}"

    return {
        "x": cx,
        "y": cy,
        "action_type": "click",
        "text_to_type": "",
        "reason": f"Detected '{label_name}' (score={scores[best_idx]:.2f})",
    }


class FasterRCNNModel(VisionModel):
    name = "faster-rcnn"

    def __init__(self):
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            self._model = _load_model()
        return self._model

    def detect_element(self, screenshot_png: bytes, intent: str, monitor_bounds=None) -> dict[str, Any]:
        import torch

        model = self._ensure_model()
        tensor = _png_to_tensor(screenshot_png)

        with torch.no_grad():
            preds = model([tensor])[0]

        # Filter low-confidence detections
        keep = preds["scores"] >= _SCORE_THRESHOLD
        filtered = {k: v[keep] for k, v in preds.items()}
        return _best_detection(filtered, intent)

    def plan_action(self, screenshot_png: bytes, prompt: str, monitor_bounds=None) -> dict[str, Any]:
        result = self.detect_element(screenshot_png, prompt, monitor_bounds)
        return {
            "action": "click",
            "x": result["x"],
            "y": result["y"],
            "reason": result.get("reason", "Faster R-CNN detection"),
        }

    def analyze(self, screenshot_png: bytes, prompt: str) -> dict[str, Any]:
        import torch

        model = self._ensure_model()
        tensor = _png_to_tensor(screenshot_png)

        with torch.no_grad():
            preds = model([tensor])[0]

        keep = preds["scores"] >= _SCORE_THRESHOLD
        detections = []
        for i in range(int(keep.sum().item())):
            box = preds["boxes"][keep][i].tolist()
            detections.append({
                "box": box,
                "score": float(preds["scores"][keep][i]),
                "label": int(preds["labels"][keep][i]),
            })
        return {"detections": detections, "count": len(detections)}


model_registry("faster-rcnn", FasterRCNNModel)
