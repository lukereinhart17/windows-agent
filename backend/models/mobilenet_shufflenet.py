"""MobileNet / ShuffleNet adapter — lightweight classification for edge / low-latency.

Setup steps
-----------
1.  pip install torch torchvision
    (CPU wheel is fine for these lightweight models.)

2.  Choose architecture via ``MOBILENET_ARCH`` env var.  Defaults to
    ``mobilenet_v3_small``.  Options:
      - mobilenet_v2
      - mobilenet_v3_small, mobilenet_v3_large
      - shufflenet_v2_x0_5, shufflenet_v2_x1_0, shufflenet_v2_x1_5

3.  For custom UI-element classification, fine-tune and point to your
    checkpoint via ``MOBILENET_CHECKPOINT``.  Set ``MOBILENET_NUM_CLASSES``
    and ``MOBILENET_LABELS`` accordingly.

Notes
-----
These are classification models (no bounding boxes).  Best used for
fast screen-state classification ("is this a login screen?") rather
than element-level detection.  Pair with a detection model for coords.
"""

from __future__ import annotations

import io
import os
from typing import Any

from .base import VisionModel
from .registry import model_registry

_ARCH = os.getenv("MOBILENET_ARCH", "mobilenet_v3_small")
_CHECKPOINT = os.getenv("MOBILENET_CHECKPOINT", "")
_NUM_CLASSES = int(os.getenv("MOBILENET_NUM_CLASSES", "1000"))
_LABELS = os.getenv("MOBILENET_LABELS", "").split(",") if os.getenv("MOBILENET_LABELS") else []

_FACTORY = {
    "mobilenet_v2": "mobilenet_v2",
    "mobilenet_v3_small": "mobilenet_v3_small",
    "mobilenet_v3_large": "mobilenet_v3_large",
    "shufflenet_v2_x0_5": "shufflenet_v2_x0_5",
    "shufflenet_v2_x1_0": "shufflenet_v2_x1_0",
    "shufflenet_v2_x1_5": "shufflenet_v2_x1_5",
}


def _load_model():
    import torch
    import torchvision.models as models

    arch = _ARCH if _ARCH in _FACTORY else "mobilenet_v3_small"
    factory_fn = getattr(models, arch)

    if _CHECKPOINT and os.path.isfile(_CHECKPOINT):
        model = factory_fn(num_classes=_NUM_CLASSES)
        model.load_state_dict(torch.load(_CHECKPOINT, map_location="cpu"))
    else:
        model = factory_fn(weights="DEFAULT")

    model.eval()
    return model


def _png_to_tensor(png_bytes: bytes):
    from torchvision import transforms
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    preprocess = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    return preprocess(img).unsqueeze(0)


class MobileNetShuffleNetModel(VisionModel):
    name = "mobilenet-shufflenet"

    def __init__(self):
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            self._model = _load_model()
        return self._model

    def _classify(self, screenshot_png: bytes) -> dict[str, Any]:
        import torch

        model = self._ensure_model()
        tensor = _png_to_tensor(screenshot_png)

        with torch.no_grad():
            logits = model(tensor)

        probs = torch.softmax(logits, dim=-1)
        top_prob, top_idx = probs.topk(1)
        idx = int(top_idx[0][0])
        label = _LABELS[idx] if idx < len(_LABELS) else f"class_{idx}"
        return {"class_index": idx, "label": label, "confidence": float(top_prob[0][0])}

    def detect_element(self, screenshot_png: bytes, intent: str, monitor_bounds=None) -> dict[str, Any]:
        classification = self._classify(screenshot_png)
        bounds = monitor_bounds or {}
        cx = bounds.get("width", 1920) // 2
        cy = bounds.get("height", 1080) // 2
        return {
            "x": cx,
            "y": cy,
            "action_type": "click",
            "text_to_type": "",
            "reason": f"Classified as '{classification['label']}' ({classification['confidence']:.2f}). "
                      "Lightweight classifier — returning screen center.",
        }

    def plan_action(self, screenshot_png: bytes, prompt: str, monitor_bounds=None) -> dict[str, Any]:
        result = self.detect_element(screenshot_png, prompt, monitor_bounds)
        return {
            "action": "click",
            "x": result["x"],
            "y": result["y"],
            "reason": result["reason"],
        }

    def analyze(self, screenshot_png: bytes, prompt: str) -> dict[str, Any]:
        import torch

        model = self._ensure_model()
        tensor = _png_to_tensor(screenshot_png)

        with torch.no_grad():
            logits = model(tensor)

        probs = torch.softmax(logits, dim=-1)
        top_probs, top_idxs = probs.topk(5)
        results = []
        for i in range(5):
            idx = int(top_idxs[0][i])
            label = _LABELS[idx] if idx < len(_LABELS) else f"class_{idx}"
            results.append({"class_index": idx, "label": label, "confidence": float(top_probs[0][i])})
        return {"top_predictions": results, "arch": _ARCH}


model_registry("mobilenet-shufflenet", MobileNetShuffleNetModel)
