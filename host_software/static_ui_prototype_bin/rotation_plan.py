from __future__ import annotations

import math
import re
from typing import Any


VALID_DIRECTIONS = {"CW", "CCW"}


def _read_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _read_float(payload: dict, *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        if key in payload and payload.get(key) not in {"", None}:
            try:
                value = float(payload.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
            return value
    return default


def normalize_angle(angle: float) -> float:
    value = float(angle) % 360.0
    if math.isclose(value, 360.0, abs_tol=1e-9):
        return 0.0
    return _round_angle(value)


def _round_angle(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if math.isclose(rounded, 0.0, abs_tol=1e-9) else rounded


def format_angle(value: float) -> str:
    value = _round_angle(value)
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def view_token(angle: float, *, closure: bool = False) -> str:
    if closure:
        return "360"
    text = format_angle(normalize_angle(angle)).replace("-", "m").replace(".", "p")
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_") or "0"
    if text.isdigit():
        return f"{int(text):03d}"
    return text


def build_capture_rotation_plan(payload: dict | None = None) -> dict:
    payload = payload or {}
    source = payload.get("sampleRotation") or payload.get("sample_rotation") or payload.get("captureRotationPlan") or payload.get("capture_rotation_plan") or payload
    if not isinstance(source, dict):
        source = {}

    enabled = _read_bool(source.get("enabled"), False)
    direction = str(source.get("direction") or "CW").strip().upper()
    if direction not in VALID_DIRECTIONS:
        direction = "CW"
    start_angle = normalize_angle(_read_float(source, "startAngleDeg", "start_angle_deg", default=0.0) or 0.0)
    include_closure = _read_bool(source.get("includeClosureView", source.get("include_closure_view")), False)

    if enabled:
        expected_interval = _read_float(source, "expectedIntervalDeg", "expected_interval_deg")
        if expected_interval is None:
            expected_interval = 30.0
        if expected_interval <= 0:
            raise ValueError("expected_interval_deg must be greater than 0")
        if expected_interval >= 360:
            view_count = 1
        else:
            view_count = max(1, int(math.ceil(360.0 / expected_interval)))
        actual_interval = 360.0 / view_count
    else:
        expected_interval = 360.0
        view_count = 1
        actual_interval = 360.0
        include_closure = False

    normal_angles = [normalize_angle(start_angle + index * actual_interval) for index in range(view_count)]
    capture_angles = list(normal_angles)
    if enabled and include_closure:
        capture_angles.append(_round_angle(start_angle + 360.0))

    views = []
    for order, angle in enumerate(capture_angles, start=1):
        closure = enabled and include_closure and order == len(capture_angles)
        token = view_token(angle, closure=closure)
        views.append(
            {
                "view_id": f"view_{token}",
                "logical_angle_deg": normalize_angle(angle),
                "mechanical_angle_deg": _round_angle(angle) if closure else normalize_angle(angle),
                "capture_order": order,
                "direction": direction,
                "closure_view": closure,
                "sample_rotation_control": "sample_stage",
                "filter_wheel_control": "independent",
            }
        )

    return {
        "enabled": enabled,
        "sample_rotation_hardware": "simulated",
        "expected_interval_deg": _round_angle(expected_interval),
        "view_count": view_count,
        "total_capture_views": len(views),
        "actual_interval_deg": _round_angle(actual_interval),
        "start_angle_deg": start_angle,
        "direction": direction,
        "include_closure_view": include_closure,
        "angles_deg": capture_angles,
        "normal_angles_deg": normal_angles,
        "closure_angle_deg": _round_angle(start_angle + 360.0) if enabled and include_closure else None,
        "returned_home": False,
        "home_status": "PENDING",
        "completed_views": [],
        "pending_views": [view["view_id"] for view in views],
        "failed_view": "",
        "rotation_domain": "sample_rotation",
        "filter_wheel_rotation_independent": True,
        "views": views,
    }


def mark_plan_completed(plan: dict) -> dict:
    updated = dict(plan)
    views = list(updated.get("views") or [])
    updated["returned_home"] = True
    updated["home_status"] = "HOME_OK"
    updated["completed_views"] = [str(view.get("view_id") or "") for view in views if view.get("view_id")]
    updated["pending_views"] = []
    updated["failed_view"] = ""
    return updated
