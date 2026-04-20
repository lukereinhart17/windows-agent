"""CNNParted adapter — partitioned CNN inference across edge + central node.

Setup steps
-----------
1.  Clone the CNNParted repository:
      git clone https://github.com/2Moles/CNNParted.git
      cd CNNParted && pip install -e .

    CNNParted is a research framework, not a pip package, so you must
    install from source.

2.  Set ``CNNPARTED_REPO=/path/to/CNNParted`` in your .env so this adapter
    can locate the framework modules.

3.  CNNParted partitions a CNN model into a "head" (runs on the edge device)
    and a "tail" (runs on a central server).  You configure:
      - ``CNNPARTED_BASE_MODEL``: backbone architecture, e.g. ``resnet50``
      - ``CNNPARTED_SPLIT_LAYER``: layer index at which to partition
      - ``CNNPARTED_CHECKPOINT``: path to your trained checkpoint

4.  Because CNNParted requires custom integration per-model and per-dataset,
    this adapter provides the structural boilerplate.  You will need to:
      a. Define your model and partition config in CNNParted's format.
      b. Train/fine-tune the partitioned model on your UI dataset.
      c. Update the ``_load_model()`` and ``_infer()`` methods below to match
         your partition topology.

Notes
-----
CNNParted is primarily a research/benchmarking tool for studying CNN
partitioning trade-offs (latency, bandwidth, accuracy).  For production
use, consider using ONNX Runtime or TensorRT for model partitioning
with optimized inference.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .base import VisionModel
from .registry import model_registry

_REPO_PATH = os.getenv("CNNPARTED_REPO", "")
_BASE_MODEL = os.getenv("CNNPARTED_BASE_MODEL", "resnet50")
_SPLIT_LAYER = int(os.getenv("CNNPARTED_SPLIT_LAYER", "4"))
_CHECKPOINT = os.getenv("CNNPARTED_CHECKPOINT", "")


class CNNPartedModel(VisionModel):
    name = "cnnparted"

    def __init__(self):
        self._head = None
        self._tail = None

    def _ensure_model(self):
        if self._head is not None:
            return

        # Add CNNParted repo to path if configured
        if _REPO_PATH and os.path.isdir(_REPO_PATH) and _REPO_PATH not in sys.path:
            sys.path.insert(0, _REPO_PATH)

        import torch
        import torchvision.models as models

        # Load the base model
        factory_fn = getattr(models, _BASE_MODEL, None)
        if factory_fn is None:
            raise ValueError(f"Unknown base model: {_BASE_MODEL}")

        base_model = factory_fn(weights="DEFAULT")

        if _CHECKPOINT and os.path.isfile(_CHECKPOINT):
            base_model.load_state_dict(torch.load(_CHECKPOINT, map_location="cpu"))

        base_model.eval()

        # Partition into head (edge) and tail (server)
        # This is a simplified split — CNNParted's actual partitioning
        # is more sophisticated and accounts for intermediate tensor sizes.
        children = list(base_model.children())
        if _SPLIT_LAYER >= len(children):
            raise ValueError(
                f"Split layer {_SPLIT_LAYER} exceeds model depth ({len(children)} layers)."
            )

        self._head = torch.nn.Sequential(*children[:_SPLIT_LAYER])
        self._tail = torch.nn.Sequential(*children[_SPLIT_LAYER:], torch.nn.Flatten())
        self._head.eval()
        self._tail.eval()

    def _infer(self, screenshot_png: bytes) -> dict[str, Any]:
        import io
        import torch
        from torchvision import transforms
        from PIL import Image

        self._ensure_model()

        img = Image.open(io.BytesIO(screenshot_png)).convert("RGB")
        preprocess = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        tensor = preprocess(img).unsqueeze(0)

        with torch.no_grad():
            # Edge inference (head)
            intermediate = self._head(tensor)
            # Server inference (tail)
            output = self._tail(intermediate)

        # For classification models, output is logits
        probs = torch.softmax(output, dim=-1)
        top_prob, top_idx = probs.topk(1)
        return {
            "class_index": int(top_idx[0][0]),
            "confidence": float(top_prob[0][0]),
            "intermediate_shape": list(intermediate.shape),
            "partition": f"head={_SPLIT_LAYER} layers, tail={len(list(self._tail.children()))} layers",
        }

    def detect_element(self, screenshot_png: bytes, intent: str, monitor_bounds=None) -> dict[str, Any]:
        result = self._infer(screenshot_png)
        bounds = monitor_bounds or {}
        cx = bounds.get("width", 1920) // 2
        cy = bounds.get("height", 1080) // 2
        return {
            "x": cx,
            "y": cy,
            "action_type": "click",
            "text_to_type": "",
            "reason": f"CNNParted classification: class {result['class_index']} "
                      f"(conf={result['confidence']:.2f}), {result['partition']}. "
                      "Returning screen center (no bbox from classifier).",
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
        return self._infer(screenshot_png)


model_registry("cnnparted", CNNPartedModel)
