from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

import numpy as np

from quality_algorithm.dataset import InsufficientTrainingDataset
from quality_algorithm.model_io import save_model_bundle
from quality_algorithm.preprocessing import fit_transform_preprocessor


PREPROCESSING_METHODS = ("RAW", "SNV", "MSC")
MODEL_TYPES = ("PLSR", "SVR", "RF")
TARGETS = ("ssc", "ta", "ph")


def load_feature_csv(path: str | Path, target: str) -> tuple[np.ndarray, np.ndarray, list[str], list[str], list[int]]:
    rows = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        feature_names = sorted([name for name in (reader.fieldnames or []) if name.startswith("R") and name[1:].isdigit()], key=lambda name: int(name[1:]))
        if not feature_names:
            raise InsufficientTrainingDataset("no spectral feature columns")
        for row in reader:
            y_text = str(row.get(target, "")).strip()
            if not y_text:
                continue
            rows.append(row)
    if len(rows) < 6:
        raise InsufficientTrainingDataset("Insufficient training dataset")
    x = np.asarray([[float(row[name]) for name in feature_names] for row in rows], dtype=np.float32)
    y = np.asarray([float(row[target]) for row in rows], dtype=np.float32)
    groups = [str(row["sample_id"]) for row in rows]
    wavelengths = [int(name[1:]) for name in feature_names]
    return x, y, groups, feature_names, wavelengths


def grouped_holdout(groups: list[str], *, test_fraction: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    unique = np.asarray(sorted(set(groups)))
    if len(unique) < 3:
        raise InsufficientTrainingDataset("at least three sample groups are required")
    rng = np.random.default_rng(42)
    shuffled = unique.copy()
    rng.shuffle(shuffled)
    test_count = max(1, int(round(len(shuffled) * test_fraction)))
    test_groups = set(shuffled[:test_count].tolist())
    train_idx = np.asarray([i for i, group in enumerate(groups) if group not in test_groups], dtype=int)
    test_idx = np.asarray([i for i, group in enumerate(groups) if group in test_groups], dtype=int)
    if len(train_idx) < 2 or len(test_idx) < 1:
        raise InsufficientTrainingDataset("group split produced too few samples")
    return train_idx, test_idx


def group_kfold_indices(groups: list[str], *, max_splits: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    unique_count = len(set(groups))
    if unique_count < 3:
        raise InsufficientTrainingDataset("at least three sample groups are required")
    from sklearn.model_selection import GroupKFold

    n_splits = min(max_splits, unique_count)
    splitter = GroupKFold(n_splits=n_splits)
    x_placeholder = np.zeros((len(groups), 1), dtype=np.float32)
    y_placeholder = np.zeros(len(groups), dtype=np.float32)
    return [(train_idx, test_idx) for train_idx, test_idx in splitter.split(x_placeholder, y_placeholder, groups)]


def fit_regressor(model_type: str, x_train: np.ndarray, y_train: np.ndarray):
    model_type = model_type.upper()
    if model_type == "PLSR":
        from sklearn.cross_decomposition import PLSRegression

        max_components = max(1, min(x_train.shape[1], len(x_train) - 1, 10))
        best = None
        best_error = math.inf
        for n_components in range(1, max_components + 1):
            model = PLSRegression(n_components=n_components)
            try:
                model.fit(x_train, y_train)
            except ValueError:
                continue
            pred = np.asarray(model.predict(x_train)).reshape(-1)
            error = float(np.mean((pred - y_train) ** 2))
            if error < best_error:
                best_error = error
                best = model
        if best is None:
            raise ValueError("PLSR training failed for all component counts")
        return best
    if model_type == "SVR":
        from sklearn.svm import SVR

        model = SVR(kernel="rbf", C=10.0, epsilon=0.05)
        model.fit(x_train, y_train)
        return model
    if model_type == "RF":
        from sklearn.ensemble import RandomForestRegressor

        model = RandomForestRegressor(n_estimators=120, random_state=42, min_samples_leaf=1)
        model.fit(x_train, y_train)
        return model
    raise ValueError(f"unsupported model type: {model_type}")


def evaluate_predictions(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    rmse = float(math.sqrt(mean_squared_error(y_true, y_pred)))
    mae = float(mean_absolute_error(y_true, y_pred))
    r2 = float(r2_score(y_true, y_pred)) if len(y_true) >= 2 else float("nan")
    rpd = None
    if len(y_true) >= 2 and rmse > 0:
        rpd = float(np.std(y_true, ddof=1) / rmse)
    return {"r2": r2, "rmse": rmse, "mae": mae, "rpd": rpd}


def train_one(
    feature_csv: str | Path,
    *,
    target: str,
    preprocessing: str,
    model_type: str,
    output_dir: str | Path | None = None,
    calibration_required: bool = False,
    validation_method: str = "GroupKFold",
) -> dict:
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    x, y, groups, feature_names, wavelengths = load_feature_csv(feature_csv, target)
    from quality_algorithm.preprocessing import transform_preprocessor

    validation_name = validation_method or "GroupKFold"
    if validation_name.lower().replace(" ", "") in {"groupkfold", "groupk-fold"}:
        y_true_parts = []
        y_pred_parts = []
        for train_idx, test_idx in group_kfold_indices(groups):
            x_train, pre_state_fold = fit_transform_preprocessor(x[train_idx], preprocessing)
            x_test = transform_preprocessor(x[test_idx], pre_state_fold)
            fold_model = fit_regressor(model_type, x_train, y[train_idx])
            y_true_parts.extend(y[test_idx].tolist())
            y_pred_parts.extend(np.asarray(fold_model.predict(x_test)).reshape(-1).tolist())
        metrics = evaluate_predictions(np.asarray(y_true_parts, dtype=np.float32), np.asarray(y_pred_parts, dtype=np.float32))
        validation_label = "GroupKFold_by_sample_id"
    else:
        train_idx, test_idx = grouped_holdout(groups)
        x_train, pre_state_holdout = fit_transform_preprocessor(x[train_idx], preprocessing)
        x_test = transform_preprocessor(x[test_idx], pre_state_holdout)
        holdout_model = fit_regressor(model_type, x_train, y[train_idx])
        pred = np.asarray(holdout_model.predict(x_test)).reshape(-1)
        metrics = evaluate_predictions(y[test_idx], pred)
        validation_label = "grouped_holdout_by_sample_id"

    x_all, pre_state = fit_transform_preprocessor(x, preprocessing)
    model = fit_regressor(model_type, x_all, y)
    metadata = {
        "target": target,
        "model_type": model_type.upper(),
        "model_version": time.strftime("%Y%m%d_%H%M%S"),
        "preprocessing": preprocessing.upper(),
        "preprocessing_state": pre_state.to_dict(),
        "wavelengths_nm": wavelengths,
        "feature_names": feature_names,
        "training_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sample_count": int(len(y)),
        "validation_method": validation_label,
        "r2": metrics["r2"],
        "rmse": metrics["rmse"],
        "mae": metrics["mae"],
        "rpd": metrics["rpd"],
        "calibration_required": bool(calibration_required),
        "software_version": "algorithm-framework-v1",
    }
    if output_dir:
        save_model_bundle(model, metadata, output_dir)
    return {"target": target, "preprocessing": preprocessing.upper(), "model": model_type.upper(), **metrics, "metadata": metadata}


def run_experiment_matrix(feature_csv: str | Path, *, targets: tuple[str, ...] = TARGETS) -> list[dict]:
    results = []
    for target in targets:
        for preprocessing in PREPROCESSING_METHODS:
            for model_type in MODEL_TYPES:
                try:
                    results.append(train_one(feature_csv, target=target, preprocessing=preprocessing, model_type=model_type))
                except InsufficientTrainingDataset:
                    raise
                except Exception as exc:
                    results.append({"target": target, "preprocessing": preprocessing, "model": model_type, "error": str(exc)})
    return sorted(results, key=lambda row: (row.get("rmse", math.inf), -row.get("r2", -math.inf) if "r2" in row else math.inf))


def write_results_csv(rows: list[dict], output_csv: str | Path) -> None:
    fields = ["target", "preprocessing", "model", "r2", "rmse", "mae", "rpd", "error"]
    with Path(output_csv).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description="Train SSC/TA/pH multispectral regression models.")
    parser.add_argument("--features", required=True, help="Feature CSV generated by build_dataset.py.")
    parser.add_argument("--target", choices=TARGETS, help="Train one target only.")
    parser.add_argument("--preprocessing", choices=PREPROCESSING_METHODS, default="RAW")
    parser.add_argument("--model", choices=MODEL_TYPES, default="PLSR")
    parser.add_argument("--output-dir", default="", help="Folder for model.joblib + metadata.json.")
    parser.add_argument("--matrix-output", default="", help="Run RAW/SNV/MSC x PLSR/SVR/RF and write result table.")
    parser.add_argument("--calibration-required", action="store_true")
    args = parser.parse_args()
    try:
        if args.matrix_output:
            rows = run_experiment_matrix(args.features, targets=(args.target,) if args.target else TARGETS)
            write_results_csv(rows, args.matrix_output)
            print(f"experiment table written: {args.matrix_output}")
            return 0
        if not args.target:
            raise ValueError("--target is required unless --matrix-output is used")
        result = train_one(
            args.features,
            target=args.target,
            preprocessing=args.preprocessing,
            model_type=args.model,
            output_dir=args.output_dir or None,
            calibration_required=args.calibration_required,
        )
    except InsufficientTrainingDataset as exc:
        print(f"Insufficient training dataset: {exc}")
        return 2
    print({k: v for k, v in result.items() if k != "metadata"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
