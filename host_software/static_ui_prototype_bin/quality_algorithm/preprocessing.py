from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


RAW = "RAW"
SNV = "SNV"
MSC = "MSC"


@dataclass
class PreprocessorState:
    method: str
    reference: list[float] | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "PreprocessorState":
        if not data:
            return cls(method=RAW)
        return cls(method=str(data.get("method", RAW)).upper(), reference=data.get("reference"))


def fit_preprocessor(x, method: str = RAW) -> PreprocessorState:
    x_arr = _as_2d(x)
    method = method.upper()
    if method == MSC:
        reference = np.mean(x_arr, axis=0).astype(float).tolist()
        return PreprocessorState(method=method, reference=reference)
    if method in {RAW, SNV}:
        return PreprocessorState(method=method)
    raise ValueError(f"unsupported preprocessing method: {method}")


def transform_preprocessor(x, state: PreprocessorState | dict | None):
    x_arr = _as_2d(x)
    state_obj = state if isinstance(state, PreprocessorState) else PreprocessorState.from_dict(state)
    method = state_obj.method.upper()
    if method == RAW:
        return x_arr.astype(np.float32)
    if method == SNV:
        mean = np.mean(x_arr, axis=1, keepdims=True)
        std = np.std(x_arr, axis=1, keepdims=True)
        return ((x_arr - mean) / np.maximum(std, 1e-8)).astype(np.float32)
    if method == MSC:
        if not state_obj.reference:
            raise ValueError("MSC reference is required")
        reference = np.asarray(state_obj.reference, dtype=np.float32)
        corrected = []
        for row in x_arr.astype(np.float32):
            slope, intercept = np.polyfit(reference, row, 1)
            if abs(float(slope)) < 1e-8:
                corrected.append(row - intercept)
            else:
                corrected.append((row - intercept) / slope)
        return np.asarray(corrected, dtype=np.float32)
    raise ValueError(f"unsupported preprocessing method: {method}")


def fit_transform_preprocessor(x, method: str = RAW):
    state = fit_preprocessor(x, method)
    return transform_preprocessor(x, state), state


def _as_2d(x):
    arr = np.asarray(x, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2:
        raise ValueError("feature matrix must be 1D or 2D")
    return arr

