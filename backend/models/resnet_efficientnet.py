"""ResNet / EfficientNet adapter — image classification via timm.

Setup steps
-----------
1.  pip install torch torchvision timm
    (use the CUDA wheel for GPU: https://pytorch.org/get-started/locally/)

2.  Choose a backbone via ``TIMM_MODEL_NAME`` env var.  Defaults to
    ``efficientnet_b0``.  Popular options:
      - resnet50, resnet101
      - efficientnet_b0, efficientnet_b3, efficientnet_b7
      - tf_efficientnetv2_s

3.  For UI-element classification you must fine-tune on a labelled dataset.
    Set ``TIMM_CHECKPOINT=/path/to/best.pth`` and ``TIMM_NUM_CLASSES=<n>``
    in your .env.  Without a checkpoint the model uses ImageNet-pretrained
    weights (useful for verifying the pipeline).

4.  Set ``TIMM_LABELS=button,textbox,icon,...`` to map output class indices
    to human-readable names.

Notes
-----
Classification models do NOT produce bounding boxes.  ``detect_element``
returns the screen center and the top predicted class.  Pair with a
detection model (Faster R-CNN, YOLO) for coordinate-level accuracy.
"""

from __future__ import annotations

import io
import os
from typing import Any

from .base import VisionModel
from .registry import model_registry

_MODEL_NAME = os.getenv("TIMM_MODEL_NAME", "efficientnet_b0")
_CHECKPOINT = os.getenv("TIMM_CHECKPOINT", "")
_NUM_CLASSES = int(os.getenv("TIMM_NUM_CLASSES", "1000"))
_LABELS = os.getenv("TIMM_LABELS", "").split(",") if os.getenv("TIMM_LABELS") else []


def _load_model():
    import timm
    import torch

    if _CHECKPOINT and os.path.isfile(_CHECKPOINT):
        model = timm.create_model(_MODEL_NAME, pretrained=False, num_classes=_NUM_CLASSES)
        model.load_state_dict(torch.load(_CHECKPOINT, map_location="cpu"))
    else:
        model = timm.create_model(_MODEL_NAME, pretrained=True)

    model.eval()
    return model


def _get_transform():
    import timm
    data_config = timm.data.resolve_data_config({}, model=_MODEL_NAME)
    return timm.data.create_transform(**data_config, is_training=False)


def _png_to_pil(png_bytes: bytes):
    from PIL import Image
    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


class ResNetEfficientNetModel(VisionModel):
    name = "resnet-efficientnet"

    def __init__(self):
        self._model = None
        self._transform = None

    def _ensure_model(self):
        if self._model is None:
            self._model = _load_model()
            self._transform = _get_transform()
        return self._model, self._transform

    def _classify(self, screenshot_png: bytes) -> dict[str, Any]:
        import torch

        model, transform = self._ensure_model()
        img = _png_to_pil(screenshot_png)
        tensor = transform(img).unsqueeze(0)

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
                      "Classification models cannot localize — returning screen center.",
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

        model, transform = self._ensure_model()
        img = _png_to_pil(screenshot_png)
        tensor = transform(img).unsqueeze(0)

        with torch.no_grad():
            logits = model(tensor)

        probs = torch.softmax(logits, dim=-1)
        top_probs, top_idxs = probs.topk(5)
        results = []
        for i in range(5):
            idx = int(top_idxs[0][i])
            label = _LABELS[idx] if idx < len(_LABELS) else f"class_{idx}"
            results.append({"class_index": idx, "label": label, "confidence": float(top_probs[0][i])})
        return {"top_predictions": results}


model_registry("resnet-efficientnet", ResNetEfficientNetModel)
