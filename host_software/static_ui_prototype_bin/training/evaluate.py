from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from quality_algorithm.model_io import load_model_bundle
from quality_algorithm.preprocessing import PreprocessorState, transform_preprocessor
from training.train import evaluate_predictions, load_feature_csv


def evaluate_model(feature_csv: str | Path, model_dir: str | Path, target: str) -> dict:
    bundle = load_model_bundle(model_dir)
    x, y, _groups, feature_names, wavelengths = load_feature_csv(feature_csv, target)
    expected_features = bundle.metadata.get("feature_names", [])
    expected_wavelengths = bundle.metadata.get("wavelengths_nm", [])
    if expected_features != feature_names or [int(v) for v in expected_wavelengths] != wavelengths:
        raise ValueError("MODEL_INPUT_MISMATCH")
    state = PreprocessorState.from_dict(bundle.metadata.get("preprocessing_state"))
    pred = np.asarray(bundle.model.predict(transform_preprocessor(x, state))).reshape(-1)
    return evaluate_predictions(y, pred)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a saved model bundle on a feature CSV.")
    parser.add_argument("--features", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--target", required=True, choices=("ssc", "ta", "ph"))
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = evaluate_model(args.features, args.model_dir, args.target)
    if args.output:
        with Path(args.output).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["r2", "rmse", "mae", "rpd"])
            writer.writeheader()
            writer.writerow(result)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

