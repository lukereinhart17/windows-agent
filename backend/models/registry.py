from __future__ import annotations

from typing import Type

from .base import VisionModel

# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_registry: dict[str, Type[VisionModel]] = {}
_instances: dict[str, VisionModel] = {}
_active_model_name: str | None = None


def register(name: str, cls: Type[VisionModel]) -> None:
    """Register a concrete VisionModel class under *name*."""
    _registry[name] = cls


def _ensure_instance(name: str) -> VisionModel:
    if name not in _instances:
        cls = _registry[name]
        _instances[name] = cls()
    return _instances[name]


def set_active_model(name: str) -> VisionModel:
    """Switch the active model.  Raises KeyError if *name* is unknown."""
    if name not in _registry:
        available = ", ".join(sorted(_registry)) or "(none)"
        raise KeyError(f"Unknown model '{name}'. Available: {available}")
    global _active_model_name
    _active_model_name = name
    return _ensure_instance(name)


def get_model(name: str) -> VisionModel:
    """Return a model instance by name. Raises KeyError if unknown."""
    if name not in _registry:
        available = ", ".join(sorted(_registry)) or "(none)"
        raise KeyError(f"Unknown model '{name}'. Available: {available}")
    return _ensure_instance(name)


def get_active_model() -> VisionModel | None:
    """Return the currently active VisionModel instance, or None."""
    if _active_model_name is None:
        return None
    return _ensure_instance(_active_model_name)


def list_models() -> list[dict[str, str]]:
    """Return metadata for every registered model."""
    return [
        {"name": name, "active": name == _active_model_name}
        for name in sorted(_registry)
    ]


# Sentinel used by model_registry decorator.
model_registry = register
