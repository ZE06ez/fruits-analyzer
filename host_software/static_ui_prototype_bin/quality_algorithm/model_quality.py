"""Software-enforced model quality gates.

The defaults are deliberately placeholder software gates. They are not
scientific acceptance criteria and must be calibrated with real experiments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from quality_algorithm.model_io import ModelInputMismatch, validate_production_model_metadata_contract


QUALITY_STATUSES = {"PASS", "WARN", "FAIL", "NOT_AVAILABLE"}
CHECK_NAMES = (
    "sample_count",
    "r2",
    "rmse",
    "mae",
    "rpd",
    "validation",
    "dataset_integrity",
    "production_contract",
    "physical_bounds",
)
VALIDATION_METHODS = {"groupkfold", "traintestsplit", "groupedholdout"}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class ModelQualityPolicy:
    schema_version: int = 1
    policy_version: str = "placeholder-v1"
    target: str = "ssc"
    minimum_sample_count: int | None = 5
    minimum_r2: float | None = None
    maximum_rmse: float | None = None
    maximum_mae: float | None = None
    minimum_rpd: float | None = None
    require_validation: bool = True
    require_dataset_version: bool = True
    allow_warn_publish: bool = False
    physical_min: float | None = None
    physical_max: float | None = None
    enabled_overrides: dict[str, bool] = field(default_factory=dict)
    blocking: dict[str, bool] = field(default_factory=lambda: {
        "sample_count": True,
        "r2": True,
        "rmse": True,
        "mae": True,
        "rpd": True,
        "validation": True,
        "dataset_integrity": True,
        "production_contract": True,
        "physical_bounds": False,
    })

    @classmethod
    def default(cls, target: str = "ssc") -> "ModelQualityPolicy":
        return cls(target=str(target or "ssc").lower())

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None, *, target: str | None = None) -> "ModelQualityPolicy":
        value = value or {}
        nested = value.get("checks") if isinstance(value.get("checks"), dict) else {}
        blocking = dict(cls.default(target or value.get("target") or "ssc").blocking)
        blocking.update({key: bool(item) for key, item in (value.get("blocking") or {}).items() if key in CHECK_NAMES})
        for key, item in nested.items():
            if isinstance(item, dict) and "blocking" in item and key in CHECK_NAMES:
                blocking[key] = bool(item["blocking"])
        enabled_overrides = {
            key: bool(item["enabled"])
            for key, item in nested.items()
            if key in CHECK_NAMES and isinstance(item, dict) and "enabled" in item
        }
        def setting(name: str, default: Any = None) -> Any:
            if name in value:
                return value[name]
            nested_value = nested.get(name)
            return nested_value.get("threshold") if isinstance(nested_value, dict) and "threshold" in nested_value else default
        return cls(
            schema_version=int(value.get("schema_version", value.get("schemaVersion", 1)) or 1),
            policy_version=str(value.get("policy_version", value.get("policyVersion", "placeholder-v1")) or "placeholder-v1"),
            target=str(target or value.get("target") or "ssc").lower(),
            minimum_sample_count=_optional_int(setting("minimum_sample_count", 5)),
            minimum_r2=_number(setting("minimum_r2")),
            maximum_rmse=_number(setting("maximum_rmse")),
            maximum_mae=_number(setting("maximum_mae")),
            minimum_rpd=_number(setting("minimum_rpd")),
            require_validation=bool(value.get("require_validation", value.get("requireValidation", True))),
            require_dataset_version=bool(value.get("require_dataset_version", value.get("requireDatasetVersion", True))),
            allow_warn_publish=bool(value.get("allow_warn_publish", value.get("allowWarnPublish", False))),
            physical_min=_number(value.get("physical_min", value.get("physicalMin"))),
            physical_max=_number(value.get("physical_max", value.get("physicalMax"))),
            enabled_overrides=enabled_overrides,
            blocking=blocking,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "target": self.target,
            "minimum_sample_count": self.minimum_sample_count,
            "minimum_r2": self.minimum_r2,
            "maximum_rmse": self.maximum_rmse,
            "maximum_mae": self.maximum_mae,
            "minimum_rpd": self.minimum_rpd,
            "require_validation": self.require_validation,
            "require_dataset_version": self.require_dataset_version,
            "allow_warn_publish": self.allow_warn_publish,
            "physical_min": self.physical_min,
            "physical_max": self.physical_max,
            "checks": {name: {"enabled": self.enabled(name), "blocking": self.is_blocking(name)} for name in CHECK_NAMES},
            "blocking": dict(self.blocking),
        }

    def enabled(self, name: str) -> bool:
        defaults = {
            "sample_count": self.minimum_sample_count is not None,
            "r2": self.minimum_r2 is not None,
            "rmse": self.maximum_rmse is not None,
            "mae": self.maximum_mae is not None,
            "rpd": self.minimum_rpd is not None,
            "validation": self.require_validation,
            "dataset_integrity": self.require_dataset_version,
            "production_contract": True,
            "physical_bounds": self.physical_min is not None or self.physical_max is not None,
        }
        return self.enabled_overrides.get(name, defaults.get(name, False))

    def is_blocking(self, name: str) -> bool:
        return bool(self.blocking.get(name, True))


@dataclass(frozen=True)
class ModelQualityReport:
    overall_status: str
    policy: ModelQualityPolicy
    checks: dict[str, dict[str, Any]]
    evaluated_at: str = field(default_factory=_timestamp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "policy_version": self.policy.policy_version,
            "policy": self.policy.to_dict(),
            "evaluated_at": self.evaluated_at,
            "checks": self.checks,
            "failed_checks": [
                name for name, check in self.checks.items()
                if check.get("enabled") and check.get("blocking") and check.get("status") in {"FAIL", "NOT_AVAILABLE"}
            ],
            "warning_checks": [
                name for name, check in self.checks.items()
                if check.get("enabled") and (
                    check.get("status") in {"WARN", "NOT_AVAILABLE"}
                    or (check.get("status") == "FAIL" and not check.get("blocking"))
                )
            ],
        }

    @property
    def publishable(self) -> bool:
        if self.overall_status == "FAIL":
            return False
        return self.overall_status != "WARN" or self.policy.allow_warn_publish


def resolve_quality_policy(source: Any, target: str) -> ModelQualityPolicy:
    if isinstance(source, ModelQualityPolicy):
        return source if source.target == target else ModelQualityPolicy.from_dict(source.to_dict(), target=target)
    if isinstance(source, dict) and isinstance(source.get("policies"), dict):
        selected = source["policies"].get(target) or source["policies"].get("default") or {}
        return ModelQualityPolicy.from_dict(selected, target=target)
    if isinstance(source, dict):
        return ModelQualityPolicy.from_dict(source, target=target)
    return ModelQualityPolicy.default(target)


def _check(name: str, *, status: str, actual: Any = None, threshold: Any = None, expected: Any = None,
           policy: ModelQualityPolicy, reason: str = "", error_code: str = "", enabled: bool = True) -> dict[str, Any]:
    return {
        "status": status,
        "actual": actual,
        "threshold": threshold,
        "expected": expected,
        "blocking": policy.is_blocking(name) if enabled else False,
        "enabled": enabled,
        "reason": reason,
        "error_code": error_code,
    }


def _disabled(name: str, policy: ModelQualityPolicy, reason: str = "disabled by policy") -> dict[str, Any]:
    return _check(name, status="NOT_AVAILABLE", policy=policy, reason=reason, enabled=False)


def _metric_check(name: str, actual: Any, threshold: float | None, *, minimum: bool, policy: ModelQualityPolicy) -> dict[str, Any]:
    if threshold is None or not policy.enabled(name):
        return _disabled(name, policy)
    number = _number(actual)
    if number is None:
        return _check(name, status="NOT_AVAILABLE", actual=None, threshold=threshold, policy=policy,
                      reason=f"{name} metric is missing from model metadata", error_code=f"MODEL_QUALITY_{name.upper()}_MISSING")
    passed = number >= threshold if minimum else number <= threshold
    return _check(
        name,
        status="PASS" if passed else "FAIL",
        actual=number,
        threshold=threshold,
        expected=f">= {threshold}" if minimum else f"<= {threshold}",
        policy=policy,
        reason="metric meets policy threshold" if passed else f"metric does not meet policy threshold: {number}",
        error_code="" if passed else f"MODEL_QUALITY_{name.upper()}_OUT_OF_RANGE",
    )


def evaluate_model_quality(
    metadata: dict[str, Any],
    *,
    model: dict[str, Any] | None = None,
    policy: ModelQualityPolicy | dict[str, Any] | None = None,
    dataset_version: dict[str, Any] | None = None,
    validation_method: str = "",
) -> ModelQualityReport:
    model = model or {}
    target = str(metadata.get("target") or model.get("target") or "ssc").lower()
    resolved = resolve_quality_policy(policy, target)
    checks: dict[str, dict[str, Any]] = {}
    sample_count = metadata.get("sample_count")
    if sample_count is None:
        checks["sample_count"] = _check("sample_count", status="NOT_AVAILABLE", policy=resolved,
                                         threshold=resolved.minimum_sample_count, reason="sample_count is missing from model metadata",
                                         error_code="MODEL_QUALITY_SAMPLE_COUNT_MISSING") if resolved.enabled("sample_count") else _disabled("sample_count", resolved)
    else:
        actual_count = _number(sample_count)
        if actual_count is None:
            checks["sample_count"] = _check("sample_count", status="NOT_AVAILABLE", actual=sample_count, policy=resolved,
                                             threshold=resolved.minimum_sample_count, reason="sample_count is not numeric",
                                             error_code="MODEL_QUALITY_SAMPLE_COUNT_INVALID")
        else:
            passed = actual_count >= int(resolved.minimum_sample_count or 0)
            checks["sample_count"] = _check("sample_count", status="PASS" if passed else "FAIL", actual=int(actual_count),
                                             threshold=resolved.minimum_sample_count, expected=f">= {resolved.minimum_sample_count}",
                                             policy=resolved, reason="sample count meets policy threshold" if passed else "sample count is below policy threshold",
                                             error_code="" if passed else "MODEL_QUALITY_SAMPLE_COUNT_TOO_LOW") if resolved.enabled("sample_count") else _disabled("sample_count", resolved)
    checks["r2"] = _metric_check("r2", metadata.get("r2", model.get("r2")), resolved.minimum_r2, minimum=True, policy=resolved)
    checks["rmse"] = _metric_check("rmse", metadata.get("rmse", model.get("rmse")), resolved.maximum_rmse, minimum=False, policy=resolved)
    checks["mae"] = _metric_check("mae", metadata.get("mae", model.get("mae")), resolved.maximum_mae, minimum=False, policy=resolved)
    checks["rpd"] = _metric_check("rpd", metadata.get("rpd", model.get("rpd")), resolved.minimum_rpd, minimum=True, policy=resolved)

    if not resolved.enabled("validation"):
        checks["validation"] = _disabled("validation", resolved)
    else:
        method = str(metadata.get("validation_method") or validation_method or "").strip()
        normalized = method.lower().replace(" ", "").replace("_", "")
        valid = any(item in normalized for item in ("groupkfold", "traintestsplit", "groupedholdout"))
        checks["validation"] = _check(
            "validation", status="PASS" if valid else "NOT_AVAILABLE", actual=method or None,
            expected="GroupKFold or TrainTestSplit", policy=resolved,
            reason="validation method is recorded" if valid else "validation method is missing or unsupported",
            error_code="" if valid else "MODEL_VALIDATION_MISSING" if not method else "MODEL_VALIDATION_INVALID",
        )

    if not resolved.enabled("dataset_integrity"):
        checks["dataset_integrity"] = _disabled("dataset_integrity", resolved)
    else:
        dataset_id = metadata.get("dataset_id") or model.get("dataset_id")
        version_id = metadata.get("dataset_version_id") or model.get("dataset_version_id")
        target_match = str(metadata.get("target") or model.get("target") or "").lower() == target
        scope_valid = bool(str(metadata.get("fruit_type") or model.get("fruit_type") or "").strip()) and bool(str(metadata.get("variety") or model.get("variety") or "generic").strip())
        version_valid = bool(version_id and dataset_version and str(dataset_version.get("dataset_id") or dataset_id) == str(dataset_id or dataset_version.get("dataset_id") or ""))
        actual = {"dataset_id": dataset_id or "", "dataset_version_id": version_id or "", "target": metadata.get("target") or model.get("target"), "fruit_type": metadata.get("fruit_type") or model.get("fruit_type"), "variety": metadata.get("variety") or model.get("variety")}
        valid = bool(dataset_id and version_valid and target_match and scope_valid)
        checks["dataset_integrity"] = _check(
            "dataset_integrity", status="PASS" if valid else "FAIL", actual=actual,
            expected="existing immutable Dataset Version with matching target/scope", policy=resolved,
            reason="dataset lineage and scope are valid" if valid else "dataset version, target, or scope is missing/incompatible",
            error_code="" if valid else "MODEL_DATASET_INTEGRITY_INVALID",
        )

    try:
        validate_production_model_metadata_contract(metadata)
        checks["production_contract"] = _check("production_contract", status="PASS", actual="production", expected="production contract", policy=resolved, reason="production model input contract is valid")
    except (ModelInputMismatch, Exception) as exc:
        checks["production_contract"] = _check("production_contract", status="FAIL", actual=None, expected="production contract", policy=resolved, reason=str(exc), error_code="MODEL_INPUT_CONTRACT_INVALID",)

    if not resolved.enabled("physical_bounds"):
        checks["physical_bounds"] = _disabled("physical_bounds", resolved)
    else:
        observed = metadata.get("target_range") or {}
        observed_min = _number(observed.get("min")) if isinstance(observed, dict) else None
        observed_max = _number(observed.get("max")) if isinstance(observed, dict) else None
        valid = (resolved.physical_min is None or observed_min is None or observed_min >= resolved.physical_min) and (resolved.physical_max is None or observed_max is None or observed_max <= resolved.physical_max)
        checks["physical_bounds"] = _check("physical_bounds", status="PASS" if valid else "FAIL", actual=observed, expected={"min": resolved.physical_min, "max": resolved.physical_max}, policy=resolved, reason="configured physical bounds are respected" if valid else "training target range exceeds configured physical bounds", error_code="" if valid else "MODEL_QUALITY_PHYSICAL_RANGE_FAILED")

    blocking_fail = any(item.get("enabled") and item.get("blocking") and item.get("status") == "FAIL" for item in checks.values())
    blocking_unavailable = any(item.get("enabled") and item.get("blocking") and item.get("status") == "NOT_AVAILABLE" for item in checks.values())
    warnings = any(
        item.get("enabled") and (
            item.get("status") in {"WARN", "NOT_AVAILABLE"}
            or (item.get("status") == "FAIL" and not item.get("blocking"))
        )
        for item in checks.values()
    )
    overall = "FAIL" if blocking_fail or blocking_unavailable else "WARN" if warnings else "PASS"
    return ModelQualityReport(overall_status=overall, policy=resolved, checks=checks)


def feature_distribution_status(values: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Deterministic feature range helper for future prediction diagnostics."""
    statistics = metadata.get("feature_statistics") or {}
    if not statistics:
        return "IN_RANGE"
    warning = False
    for name, value in values.items():
        stats = statistics.get(name)
        number = _number(value)
        if not isinstance(stats, dict) or number is None:
            warning = True
            continue
        minimum = _number(stats.get("min"))
        maximum = _number(stats.get("max"))
        if minimum is not None and number < minimum or maximum is not None and number > maximum:
            return "OUT_OF_RANGE"
    return "WARNING" if warning else "IN_RANGE"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
