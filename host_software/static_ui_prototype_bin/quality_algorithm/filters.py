from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CONFIG_PATH = Path(__file__).with_name("filter_config.development.json")


@dataclass(frozen=True)
class FilterBand:
    filter_position: int
    wavelength_nm: int
    bandwidth_nm: float | None = None
    exposure_ms: float | None = None
    gain: float | None = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "FilterBand":
        position = data.get("filter_position", data.get("position"))
        if position is None:
            raise ValueError("filter_position is required")
        wavelength = data.get("wavelength_nm")
        if wavelength is None:
            raise ValueError("wavelength_nm is required")
        return cls(
            filter_position=int(position),
            wavelength_nm=int(wavelength),
            bandwidth_nm=_optional_float(data.get("bandwidth_nm")),
            exposure_ms=_optional_float(data.get("exposure_ms")),
            gain=_optional_float(data.get("gain")),
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict:
        return {
            "filter_position": self.filter_position,
            "wavelength_nm": self.wavelength_nm,
            "bandwidth_nm": self.bandwidth_nm,
            "exposure_ms": self.exposure_ms,
            "gain": self.gain,
            "enabled": self.enabled,
        }


def load_filter_config(path: str | Path | None = None) -> list[FilterBand]:
    config_path = Path(path).expanduser() if path else CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    bands = [FilterBand.from_dict(item) for item in payload.get("filters", [])]
    if not bands:
        raise ValueError(f"no filters configured in {config_path}")
    return sorted(bands, key=lambda band: (band.filter_position, band.wavelength_nm))


def enabled_bands(filters: Iterable[FilterBand] | None = None) -> list[FilterBand]:
    bands = list(filters) if filters is not None else load_filter_config()
    return sorted([band for band in bands if band.enabled], key=lambda band: band.wavelength_nm)


def expected_wavelengths(filters: Iterable[FilterBand] | None = None) -> list[int]:
    return [band.wavelength_nm for band in enabled_bands(filters)]


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

