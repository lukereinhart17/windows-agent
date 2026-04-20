from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from typing import Any

try:
    from .models import get_active_model, get_model
except ImportError:
    from models import get_active_model, get_model


@dataclass
class PipelineConfig:
    mode: str = "single"  # single | cascade
    detector_model: str = "yolo"
    classifier_model: str = "mobilenet-shufflenet"
    planner_model: str = "gemini"
    verify_before_click: bool = True
    verification_threshold: float = 0.8
    fallback_single_on_low_confidence: bool = True
    verifier_model: str = "gemini"


@dataclass
class PipelineResult:
    plan: dict[str, Any]
    debug: dict[str, Any]


def _safe_step_timing(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    return result, duration_ms


def _coerce_confidence(value: Any, default: float = 0.0) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(conf, 1.0))


def _verify_plan_candidate(
    screenshot_png: bytes,
    user_prompt: str,
    candidate_plan: dict[str, Any],
    monitor_bounds: dict[str, int],
    config: PipelineConfig,
    fallback_model_name: str,
) -> tuple[dict[str, Any], float]:
    verifier_name = (config.verifier_model or "").strip() or fallback_model_name
    try:
        verifier = get_model(verifier_name)
    except KeyError:
        verifier = get_model(fallback_model_name)

    verify_prompt = (
        "You are a strict click verifier for a desktop UI automation agent. "
        "Given the user request, screenshot dimensions, and a candidate click coordinate, "
        "decide whether this candidate lands on the correct target. "
        "Return ONLY JSON with this schema: "
        '{"ok": true|false, "confidence": 0.0-1.0, "reason": "short reason"}.\n'
        f"User request: {user_prompt}\n"
        f"Candidate action: {candidate_plan}\n"
        f"Screenshot bounds: {monitor_bounds}"
    )

    raw_verification = verifier.analyze(screenshot_png, verify_prompt)
    ok = bool(raw_verification.get("ok", False))
    confidence = _coerce_confidence(raw_verification.get("confidence"), default=0.0)
    reason = str(raw_verification.get("reason", "")).strip()

    verification = {
        "verifier": verifier.name,
        "ok": ok,
        "confidence": confidence,
        "threshold": config.verification_threshold,
        "reason": reason,
    }
    return verification, confidence


def plan_action_with_pipeline(
    screenshot_png: bytes,
    prompt: str,
    monitor_bounds: dict[str, int],
    config: PipelineConfig,
) -> PipelineResult:
    """Plan an action using either a single active model or a cascaded pipeline."""
    if config.mode == "single":
        active = get_active_model()
        if active is None:
            raise ValueError("No active model selected.")
        plan, planner_ms = _safe_step_timing(active.plan_action, screenshot_png, prompt, monitor_bounds)

        action = str(plan.get("action", "click")).lower()
        verification_debug: dict[str, Any] | None = None
        fallback_debug: dict[str, Any] | None = None
        verification_ms = 0.0

        if config.verify_before_click and action in {"click", "move"}:
            verification, verification_ms = _safe_step_timing(
                _verify_plan_candidate,
                screenshot_png,
                prompt,
                plan,
                monitor_bounds,
                config,
                active.name,
            )
            verification_debug = verification

            if (not verification["ok"]) or verification["confidence"] < config.verification_threshold:
                if config.fallback_single_on_low_confidence:
                    try:
                        fallback_planner = get_model("gemini")
                    except KeyError:
                        fallback_planner = active

                    fallback_plan, fallback_ms = _safe_step_timing(
                        fallback_planner.plan_action,
                        screenshot_png,
                        prompt,
                        monitor_bounds,
                    )
                    fallback_verification, fallback_verify_ms = _safe_step_timing(
                        _verify_plan_candidate,
                        screenshot_png,
                        prompt,
                        fallback_plan,
                        monitor_bounds,
                        config,
                        fallback_planner.name,
                    )
                    verification_ms = round(verification_ms + fallback_verify_ms, 2)
                    fallback_debug = {
                        "planner": fallback_planner.name,
                        "plan": fallback_plan,
                        "latency_ms": fallback_ms,
                        "verification": fallback_verification,
                    }

                    if fallback_verification["ok"] and (
                        fallback_verification["confidence"] >= config.verification_threshold
                    ):
                        plan = fallback_plan
                    else:
                        raise ValueError(
                            "Verification failed for click candidate and fallback re-plan. "
                            f"Confidence remained below threshold ({config.verification_threshold})."
                        )
                else:
                    raise ValueError(
                        "Verification rejected click candidate. "
                        f"Confidence below threshold ({config.verification_threshold})."
                    )

        return PipelineResult(
            plan=plan,
            debug={
                "mode": "single",
                "planner": active.name,
                "latency_ms": {
                    "planner": planner_ms,
                    "verification": round(verification_ms, 2),
                },
                "verification": verification_debug,
                "fallback": fallback_debug,
            },
        )

    detector = get_model(config.detector_model)
    classifier = get_model(config.classifier_model)
    planner = get_model(config.planner_model)

    detection, detector_ms = _safe_step_timing(
        detector.detect_element,
        screenshot_png,
        prompt,
        monitor_bounds,
    )
    classification, classifier_ms = _safe_step_timing(
        classifier.analyze,
        screenshot_png,
        f"Classify screen context for request: {prompt}",
    )

    planner_prompt = (
        f"User request: {prompt}\n"
        f"Detector suggestion: {detection}\n"
        f"Classifier context: {classification}\n"
        "Use the detector coordinates unless classifier context strongly contradicts them."
    )
    plan, planner_ms = _safe_step_timing(
        planner.plan_action,
        screenshot_png,
        planner_prompt,
        monitor_bounds,
    )

    # Fall back to detector coordinates if planner output is incomplete.
    if "x" not in plan or "y" not in plan:
        plan["x"] = int(detection.get("x", 0))
        plan["y"] = int(detection.get("y", 0))
    if "action" not in plan:
        plan["action"] = "click"
    if "reason" not in plan:
        plan["reason"] = "Cascaded plan generated from detector + classifier + planner."

    action = str(plan.get("action", "click")).lower()
    verification_debug: dict[str, Any] | None = None
    fallback_debug: dict[str, Any] | None = None
    verification_ms = 0.0

    if config.verify_before_click and action in {"click", "move"}:
        verification, verification_ms = _safe_step_timing(
            _verify_plan_candidate,
            screenshot_png,
            prompt,
            plan,
            monitor_bounds,
            config,
            planner.name,
        )
        verification_debug = verification

        if (not verification["ok"]) or verification["confidence"] < config.verification_threshold:
            if config.fallback_single_on_low_confidence:
                try:
                    fallback_planner = get_model("gemini")
                except KeyError:
                    fallback_planner = planner

                fallback_plan, fallback_ms = _safe_step_timing(
                    fallback_planner.plan_action,
                    screenshot_png,
                    prompt,
                    monitor_bounds,
                )
                fallback_verification, fallback_verify_ms = _safe_step_timing(
                    _verify_plan_candidate,
                    screenshot_png,
                    prompt,
                    fallback_plan,
                    monitor_bounds,
                    config,
                    fallback_planner.name,
                )
                verification_ms = round(verification_ms + fallback_verify_ms, 2)
                fallback_debug = {
                    "planner": fallback_planner.name,
                    "plan": fallback_plan,
                    "latency_ms": fallback_ms,
                    "verification": fallback_verification,
                }

                if fallback_verification["ok"] and (
                    fallback_verification["confidence"] >= config.verification_threshold
                ):
                    plan = fallback_plan
                else:
                    raise ValueError(
                        "Verification failed for click candidate and fallback re-plan. "
                        f"Confidence remained below threshold ({config.verification_threshold})."
                    )
            else:
                raise ValueError(
                    "Verification rejected click candidate. "
                    f"Confidence below threshold ({config.verification_threshold})."
                )

    return PipelineResult(
        plan=plan,
        debug={
            "mode": "cascade",
            "detector": detector.name,
            "classifier": classifier.name,
            "planner": planner.name,
            "latency_ms": {
                "detector": detector_ms,
                "classifier": classifier_ms,
                "planner": planner_ms,
                "verification": round(verification_ms, 2),
                "total": round(detector_ms + classifier_ms + planner_ms, 2),
            },
            "detector_output": detection,
            "classifier_output": classification,
            "verification": verification_debug,
            "fallback": fallback_debug,
        },
    )


def detect_step_with_pipeline(
    screenshot_png: bytes,
    intent: str,
    monitor_bounds: dict[str, int],
    config: PipelineConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an executor-compatible action dict from pipeline output."""
    result = plan_action_with_pipeline(screenshot_png, intent, monitor_bounds, config)
    plan = result.plan
    action_type = str(plan.get("action", "click")).lower()
    if action_type not in {"click", "type", "scroll", "move"}:
        action_type = "click"

    action = {
        "x": int(plan.get("x", 0)),
        "y": int(plan.get("y", 0)),
        "action_type": "click" if action_type == "move" else action_type,
        "text_to_type": str(plan.get("text_to_type", "")),
    }
    return action, result.debug


def config_to_dict(config: PipelineConfig) -> dict[str, str]:
    return asdict(config)
