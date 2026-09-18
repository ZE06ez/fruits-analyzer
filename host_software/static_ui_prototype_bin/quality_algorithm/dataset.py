from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .filters import FilterBand, expected_wavelengths
from .analysis_pipeline import FeaturePipelineConfig, run_feature_pipeline
from .spectral_features import FeatureExtractionError


@dataclass
class LabelRecord:
    sample_id: str
    ssc: float | None = None
    ta: float | None = None
    ph: float | None = None


class InsufficientTrainingDataset(RuntimeError):
    pass


def read_labels_csv(path: str | Path) -> dict[str, LabelRecord]:
    labels: dict[str, LabelRecord] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "sample_id" not in reader.fieldnames:
            raise ValueError("labels.csv must include sample_id")
        for row in reader:
            sample_id = (row.get("sample_id") or "").strip()
            if not sample_id:
                continue
            labels[sample_id] = LabelRecord(
                sample_id=sample_id,
                ssc=_optional_float(row.get("ssc")),
                ta=_optional_float(row.get("ta")),
                ph=_optional_float(row.get("ph")),
            )
    return labels


def build_feature_rows(
    samples_root: str | Path,
    labels_csv: str | Path,
    *,
    filters: list[FilterBand] | None = None,
    allow_uncalibrated: bool = True,
    pipeline_config: FeaturePipelineConfig | dict | None = None,
) -> list[dict]:
    root = Path(samples_root).expanduser()
    labels = read_labels_csv(labels_csv)
    rows: list[dict] = []
    for sample_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        label = labels.get(sample_dir.name)
        if label is None:
            continue
        try:
            config = pipeline_config if pipeline_config is not None else FeaturePipelineConfig.legacy(filters=filters, require_calibration=not allow_uncalibrated)
            record = run_feature_pipeline(sample_dir, config=config)
        except FeatureExtractionError:
            continue
        row = {"sample_id": record.sample_id}
        for wavelength, value in zip(record.wavelengths, record.features):
            row[f"R{wavelength}"] = value
        row.update({"ssc": label.ssc, "ta": label.ta, "ph": label.ph})
        rows.append(row)
    if not rows:
        raise InsufficientTrainingDataset("Insufficient training dataset")
    return rows


def write_feature_csv(rows: list[dict], output_csv: str | Path, wavelengths: list[int] | None = None) -> None:
    if not rows:
        raise InsufficientTrainingDataset("Insufficient training dataset")
    if wavelengths is None:
        wavelengths = sorted(int(key[1:]) for key in rows[0] if key.startswith("R") and key[1:].isdigit())
    fields = ["sample_id"] + [f"R{w}" for w in wavelengths] + ["ssc", "ta", "ph"]
    with Path(output_csv).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _optional_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)

